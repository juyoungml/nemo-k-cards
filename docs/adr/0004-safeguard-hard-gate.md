# The Safeguard is a binding check, not advice

Status: accepted (2026-10-29, approved by the product owner)

## Context

Every automated check is advisory (BACKEND §11 #4: "never auto-reject"). Blocking Issues are shown in Review, and an
Approver can still approve with an Override reason. For harmful content (mocking a Korean tragedy, hate, sexual
content, dangerous or illegal activity, and similar) one wrong click posts publicly to the Organization's
Instagram account, and a deleted post is still screenshotted.

## Decision

- A dedicated Safeguard stage runs after review, for Quick and Brainstorm Jobs. It combines host rules (the
  `severity: block` phrases in `sensitive_topics.yaml`) with a Claude subagent that reads the text, hashtags and
  rendered images against `policies/content/safeguard_policy.md`.
- A `block` verdict is binding. The Job ends `BLOCKED`, `POST /jobs/{id}/approve` returns 409 for every Role, with or
  without an `override_reason`, and the Admin disables Approve. The only options are Regenerate or Reject. An
  Override applies to blocking Issues only, never to a Safeguard block.
- The host decides: any block finding blocks even if the model says "allow", and the model can't overrule a
  rule hit.
- Fail closed: if the model errors or times out, the Deck is blocked and the explanation says so.
- The verdict is tied to a hash of everything that will be posted: slide text, caption, image choices and the
  rendered slides. If the Approver edits the caption, the edit is checked on a copy (rules at Approve, then the
  full check before upload). A refused edit is recorded and shown, and the Deck keeps its last allowed caption, so
  one bad edit never kills a good Deck. A Job without a verdict can't be approved.
- A real post (`graph`) of content the model hasn't checked is refused on a server that can't run the model
  (`DEMO_MODE=fixture`). Mock and dry-run publishes may rely on the phrase rules alone.
- Every block carries an explanation for the Approver: a summary, plus for each finding the exact excerpt,
  the rule, why it is harmful, and a fix.
- Blocks and refused approvals go to the audit log (`safeguard.blocked`, `safeguard.edit_blocked`,
  `deck.approve_refused`).
- Everything else (facts, links, visual QA, tone, PII) stays advisory, as before.

## Consequences

- False positives cost a regeneration, not a post. If the model over-blocks, tune `safeguard_policy.md`
  (the policy version shows in Review). Don't add an Override.
- `GLOSSARY.md` defines Safeguard, Blocked Deck and Safeguard finding; `docs/BACKEND.md` v1.3 carries the contract.
- The stage list changed, so the pipeline's `WORKFLOW_VERSION` went to 2: a Job still running under version 1 at a
  restart is marked failed (ADR 0002), and Decks that reached review before this change have no verdict and must be
  regenerated before they can be published.
