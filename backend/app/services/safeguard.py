"""Safeguard: the hard gate that blocks harmful content before it can be published.

Two layers, both of which can block:
1. Rules (host, deterministic): every `severity: block` phrase in policies/content/sensitive_topics.yaml.
   The model can't overrule these.
2. Model (the `safeguard` subagent): reads the slide text, caption, hashtags and the rendered images against
   policies/content/safeguard_policy.md, and decides allow/block with an explanation for the operator.

The gate fails closed. If the model can't run or returns unusable output, the deck is blocked, and the
summary says so. The verdict carries a hash of the content it checked, so an edited caption must be
checked again before publishing.
"""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import REPO_ROOT
from app.schemas import CardDeck, SafeguardFinding, SafeguardModelOutput, SafeguardVerdict

POLICY_DIR = REPO_ROOT / "policies/content"
POLICY_MD = POLICY_DIR / "safeguard_policy.md"
SENSITIVE_YAML = POLICY_DIR / "sensitive_topics.yaml"
HASHTAG = re.compile(r"#[\w가-힣]+")

ModelCall = Callable[[dict, list[Path]], Awaitable[SafeguardModelOutput]]


@dataclass(frozen=True)
class Policy:
    markdown: str
    sensitive_yaml: str
    version: str
    phrases: tuple[tuple[re.Pattern, str], ...]   # (pattern, note) for severity: block


@lru_cache(maxsize=1)
def _load(md_mtime: float, yaml_mtime: float) -> Policy:
    md = POLICY_MD.read_text(encoding="utf-8")
    raw = SENSITIVE_YAML.read_text(encoding="utf-8")
    phrases = tuple((re.compile(p["pattern"]), p.get("note", ""))
                    for p in (yaml.safe_load(raw) or {}).get("phrases", []) if p.get("severity") == "block")
    version = hashlib.sha256((md + "\0" + raw).encode()).hexdigest()[:7]
    return Policy(md, raw, version, phrases)


def load_policy() -> Policy:
    """Reloads when either policy file changes on disk, so a policy edit needs no restart."""
    return _load(POLICY_MD.stat().st_mtime, SENSITIVE_YAML.stat().st_mtime)


def content_hash(deck: CardDeck) -> str:
    """What the verdict vouches for: every word that will be posted (slide text + caption)."""
    parts = [f"{s.index}\x1f{s.heading}\x1f{s.body}" for s in sorted(deck.slides, key=lambda s: s.index)]
    return hashlib.sha256("\x1e".join([*parts, deck.caption]).encode()).hexdigest()[:16]


def _texts(deck: CardDeck) -> list[tuple[str, int | None, str]]:
    """(location, slide_index, text) for every piece of posted text."""
    out: list[tuple[str, int | None, str]] = [("slide", s.index, f"{s.heading}\n{s.body}") for s in deck.slides]
    tags = HASHTAG.findall(deck.caption)
    caption_wo_tags = HASHTAG.sub("", deck.caption)
    out.append(("caption", None, caption_wo_tags))
    if tags:
        out.append(("hashtags", None, " ".join(tags)))
    return out


def rule_findings(deck: CardDeck, policy: Policy | None = None) -> list[SafeguardFinding]:
    policy = policy or load_policy()
    found: list[SafeguardFinding] = []
    for location, idx, text in _texts(deck):
        for rx, note in policy.phrases:
            if m := rx.search(text):
                found.append(SafeguardFinding(
                    category=_category(note), severity="block", location=location, slide_index=idx,
                    excerpt=m.group(0), rule=f"sensitive_topics.yaml · blocked phrase /{rx.pattern}/",
                    explanation=(f"\"{m.group(0)}\" is on the blocked-phrase list: {note}. Posting it would read "
                                 "as mocking a Korean tragedy or taking a side on a sensitive issue."
                                 if note else f"\"{m.group(0)}\" is on the blocked-phrase list."),
                    fix=f"Remove or reword \"{m.group(0)}\" and generate again."))
    return found


def _category(note: str) -> str:
    return "territorial_cultural" if re.search(r"East Sea|Dokdo", note, re.IGNORECASE) else "historical_tragedy"


def _block_summary(rules: list[SafeguardFinding], findings: list[SafeguardFinding],
                   out: SafeguardModelOutput) -> str:
    """Lead with the rule hits (the model is told not to repeat them), then the model's own reasoning."""
    lead = ""
    if rules:
        lead = "Blocked phrase " + ", ".join(sorted({f'"{f.excerpt}"' for f in rules})) + \
            " can't be posted (see below)."
    if out.decision == "block" and out.summary:
        return f"{lead} {out.summary}".strip()
    if lead:
        return lead
    blocks = [f for f in findings if f.severity == "block"]
    more = f" ({len(blocks) - 1} more below)" if len(blocks) > 1 else ""
    return f"Blocked: {blocks[0].explanation}{more}"


async def check(deck: CardDeck, *, slide_paths: list[Path], today: date,
                model: ModelCall | None) -> SafeguardVerdict:
    """Run the rules and (if given) the model, and merge them into one binding verdict.

    `model=None` means rules only (fixture re-checks). A model that raises fails closed.
    """
    policy = load_policy()
    rules = rule_findings(deck, policy)
    out = SafeguardModelOutput(decision="allow", summary="")
    checked_by: list = ["rules"]
    error = None
    if model is not None:
        payload = {"deck": deck.model_dump(mode="json"), "slide_paths": [str(p) for p in slide_paths],
                   "policy_md": policy.markdown, "sensitive_topics_yaml": policy.sensitive_yaml,
                   "today": today.isoformat(), "rule_findings": [f.model_dump() for f in rules]}
        try:
            out = await model(payload, slide_paths)
            checked_by.append("model")
        except Exception as e:  # noqa: BLE001 — any failure means "not proven safe"
            error = str(e)[:300]
    findings = rules + out.findings
    if error:
        decision = "block"
        summary = ("Blocked because the safety check could not run, so this deck is not proven safe. "
                   "Regenerate it, or reject it.")
    else:
        # The host decides: any block finding blocks, even if the model said "allow".
        decision = "block" if out.decision == "block" or any(f.severity == "block" for f in findings) \
            else "allow"
        summary = _block_summary(rules, findings, out) if decision == "block" else (
            out.summary or "No harmful content found by the safety rules.")
    return SafeguardVerdict(decision=decision, summary=summary, findings=findings, checked_by=checked_by,
                            model_error=error, policy_version=policy.version, content_hash=content_hash(deck),
                            checked_at=datetime.now(UTC))
