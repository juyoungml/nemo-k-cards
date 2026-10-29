# 0001 · The safeguard is a hard gate, not advice

Status: accepted (2026-10-29)

## Context

Every automated check used to be advisory (BACKEND §11 #4: "never auto-reject"). Blocking issues were shown in
Review, and the operator could still press "Approve anyway". For harmful content (mocking a Korean tragedy,
hate, sexual content, dangerous or illegal activity, and similar) one wrong click posts publicly to
@whatsonkorea, and a deleted post is still screenshotted.

## Decision

- A dedicated `safeguard` stage runs after review. It combines host rules (the `severity: block` phrases in
  `sensitive_topics.yaml`) with a Claude subagent that reads the text, hashtags and rendered images against
  `policies/content/safeguard_policy.md`.
- A `block` verdict is binding. The job ends `BLOCKED`, `POST /jobs/{id}/approve` returns 409, and the Admin
  disables Approve. The only options are Regenerate or Reject. There is no override.
- The host decides: any block finding blocks even if the model says "allow", and the model can't overrule a
  rule hit.
- Fail closed: if the model errors or times out, the deck is blocked and the explanation says so.
- The verdict is tied to a hash of the posted text. If the operator edits the caption, it is checked again
  (rules at approve, then the full check before upload). A job without a verdict can't be approved.
- Every block carries an explanation for the operator: a summary, plus for each finding the exact excerpt,
  the rule, why it is harmful, and a fix.
- Everything else (facts, links, visual QA, tone, PII) stays advisory, as before.

## Consequences

- False positives cost a regeneration, not a post. If the model over-blocks, tune `safeguard_policy.md`
  (the policy version shows in Review). Don't add an override.
- `docs/spec/v1.md` plans "override with reason" for blocking issues. That applies to the advisory checks,
  not to the safeguard.
- Jobs created before this change have no verdict and must be regenerated before they can be published.
