"""Safeguard: the binding check that blocks harmful content before it can be approved (ADR 0004).

Two layers, both of which can block:
1. Rules (host, deterministic): every `severity: block` phrase in policies/content/sensitive_topics.yaml.
   The model can't overrule these.
2. Model (the `safeguard` subagent): reads the slide text, caption, hashtags and the rendered images against
   policies/content/safeguard_policy.md, and decides allow/block with an explanation for the Approver.

The gate fails closed. If the model can't run or returns unusable output, the Deck is blocked, and the
summary says so. The verdict carries a hash of the content it checked (text, image choices and the rendered
slide files), so anything that changes after the check must be checked again before publishing.

Nothing here touches the store: the caller (the pipeline, the approve route, publishing) owns the Job and its
Organization.
"""

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import TypeAdapter

from app.config import REPO_ROOT
from app.schemas import (
    CardDeck,
    HarmCategory,
    SafeguardFinding,
    SafeguardModelOutput,
    SafeguardVerdict,
)
from app.services.visual_qa import HASHTAG

POLICY_DIR = REPO_ROOT / "policies/content"
POLICY_MD = POLICY_DIR / "safeguard_policy.md"
SENSITIVE_YAML = POLICY_DIR / "sensitive_topics.yaml"
RULE_PREFIX = "sensitive_topics.yaml · blocked phrase"  # how a host-rule finding names its rule
_CATEGORY = TypeAdapter(HarmCategory)

ModelCall = Callable[[dict, list[Path]], Awaitable[SafeguardModelOutput]]


@dataclass(frozen=True)
class BlockedPhrase:
    pattern: re.Pattern
    category: HarmCategory
    note: str


@dataclass(frozen=True)
class Policy:
    markdown: str
    sensitive_yaml: str
    version: str
    phrases: tuple[BlockedPhrase, ...]   # every `severity: block` phrase


@lru_cache(maxsize=4)
def _load(md_path: Path, yaml_path: Path, md_mtime: float, yaml_mtime: float) -> Policy:
    md = md_path.read_text(encoding="utf-8")
    raw = yaml_path.read_text(encoding="utf-8")
    # A missing or unknown `category` raises here: a broken policy must fail loudly, not block nothing.
    phrases = tuple(BlockedPhrase(re.compile(p["pattern"]), _CATEGORY.validate_python(p["category"]),
                                  p.get("note", ""))
                    for p in (yaml.safe_load(raw) or {}).get("phrases", []) if p.get("severity") == "block")
    version = hashlib.sha256((md + "\0" + raw).encode()).hexdigest()[:7]
    return Policy(md, raw, version, phrases)


def load_policy() -> Policy:
    """Reloads when either policy file changes on disk, so a policy edit needs no restart."""
    return _load(POLICY_MD, SENSITIVE_YAML, POLICY_MD.stat().st_mtime, SENSITIVE_YAML.stat().st_mtime)


def content_hash(deck: CardDeck, slide_files: Sequence[Path] = ()) -> str:
    """What the verdict vouches for: every word, every image choice and every rendered slide that will be posted."""
    h = hashlib.sha256()
    for s in sorted(deck.slides, key=lambda s: s.index):
        image = s.image.url if s.image else ""
        h.update(f"{s.index}\x1f{s.heading}\x1f{s.body}\x1f{image}\x1f{s.stock_id or ''}\x1e".encode())
    h.update(deck.caption.encode())
    for f in sorted(slide_files, key=lambda p: p.name):
        h.update(b"\x1d" + f.name.encode() + b"\x1f" + hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:16]


def texts(deck: CardDeck) -> list[tuple[str, int | None, str]]:
    """(location, slide_index, text) for every piece of posted text."""
    out: list[tuple[str, int | None, str]] = [("slide", s.index, f"{s.heading}\n{s.body}") for s in deck.slides]
    tags = HASHTAG.findall(deck.caption)
    out.append(("caption", None, HASHTAG.sub("", deck.caption)))
    if tags:
        out.append(("hashtags", None, " ".join(tags)))
    return out


def rule_findings(deck: CardDeck, policy: Policy | None = None) -> list[SafeguardFinding]:
    policy = policy or load_policy()
    found: list[SafeguardFinding] = []
    for location, idx, text in texts(deck):
        for p in policy.phrases:
            if m := p.pattern.search(text):
                hit = m.group(0)
                found.append(SafeguardFinding(
                    category=p.category, severity="block", location=location, slide_index=idx, excerpt=hit,
                    rule=f"{RULE_PREFIX} /{p.pattern.pattern}/",
                    explanation=(f"\"{hit}\" is on the blocked-phrase list: {p.note}. Posting it would read as "
                                 "mocking a Korean tragedy or taking a side on a sensitive issue."
                                 if p.note else f"\"{hit}\" is on the blocked-phrase list."),
                    fix=f"Remove or reword \"{hit}\" and generate again."))
    return found


def is_rule_finding(f: SafeguardFinding) -> bool:
    return f.rule.startswith(RULE_PREFIX)


def model_part(v: SafeguardVerdict) -> SafeguardModelOutput | None:
    """The model's own output inside a stored verdict (its findings, without the host's rule hits)."""
    if v.model_decision is None:
        return None
    return SafeguardModelOutput(decision=v.model_decision, summary="",
                                findings=[f for f in v.findings if not is_rule_finding(f)])


def _block_summary(rules: list[SafeguardFinding], findings: list[SafeguardFinding],
                   out: SafeguardModelOutput) -> str:
    """Lead with the rule hits (the model is told not to repeat them), then the model's own reasoning."""
    lead = ""
    if rules:
        lead = "Blocked phrase " + ", ".join(sorted({f'"{f.excerpt}"' for f in rules})) + " can't be posted."
    if out.decision == "block" and out.summary:
        return f"{lead} {out.summary}".strip()
    if lead:
        return lead
    blocks = [f for f in findings if f.severity == "block"]
    more = f" ({len(blocks) - 1} more below)" if len(blocks) > 1 else ""
    return f"Blocked: {blocks[0].explanation}{more}"


def merge(rules: list[SafeguardFinding], out: SafeguardModelOutput | None,
          error: str | None = None) -> tuple[Literal["allow", "block"], str, list[SafeguardFinding]]:
    """The host decides: any block finding blocks even if the model said "allow", the model can't remove a rule
    hit, and a model that failed blocks (fail closed). `out=None` means rules only. → (decision, summary, findings)"""
    out = out or SafeguardModelOutput(decision="allow", summary="")
    findings = rules + out.findings
    if error:
        return ("block", ("Blocked because the safety check could not run, so this Deck is not proven safe. "
                          "Regenerate it, or reject it."), findings)
    if out.decision == "block" or any(f.severity == "block" for f in findings):
        return "block", _block_summary(rules, findings, out), findings
    return "allow", out.summary or "No harmful content found by the safety rules.", findings


async def check(deck: CardDeck, *, slide_paths: list[Path], today: date,
                model: ModelCall | None) -> SafeguardVerdict:
    """Run the rules and (if given) the model, and merge them into one binding verdict.

    `model=None` means rules only (a quick re-check of a caption edit). A model that raises fails closed.
    """
    policy = load_policy()
    rules = rule_findings(deck, policy)
    out: SafeguardModelOutput | None = None
    checked_by: list[Literal["rules", "model"]] = ["rules"]
    error = None
    if model is not None:
        payload = {"deck": deck.model_dump(mode="json"), "slide_paths": [str(p) for p in slide_paths],
                   "policy_md": policy.markdown, "sensitive_topics_yaml": policy.sensitive_yaml,
                   "today": today.isoformat(), "rule_findings": [f.model_dump() for f in rules]}
        try:
            out = await model(payload, slide_paths)
            checked_by.append("model")
        except Exception as e:  # noqa: BLE001 — any failure means "not proven safe"
            error = str(e)[:300] or type(e).__name__
    decision, summary, findings = merge(rules, out, error)
    return SafeguardVerdict(decision=decision, summary=summary, findings=findings, checked_by=checked_by,
                            model_decision=out.decision if out else None, model_error=error,
                            policy_version=policy.version, content_hash=content_hash(deck, slide_paths),
                            checked_at=datetime.now(UTC))
