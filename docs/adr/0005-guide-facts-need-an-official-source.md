# Guide facts need an official source, decided by the host

Status: accepted (2026-10-30, chosen by the product owner for #27)

## Context

A Guide Deck (GLOSSARY "Deck kind") tells readers how to do something or what the rules are: transit cards, entry
rules, tax refunds, an Organization's new opening hours. There is no event page to send them to, and wrong advice
here costs a reader money or a missed flight. GLOSSARY "Official source" says every Guide fact must trace to a
government or agency site, an operator's own site, or the Organization's Source material. The researcher labels each
source's `kind`, but a label is only the agent's claim.

## Decision

The host decides, per fact, at Verify (`backend/app/official.py`, `backend/app/guides.py`):

- A link on a known government, agency or public-operator domain counts (an allowlist of suffixes such as `go.kr`
  and named domains such as `visitkorea.or.kr`, `t-money.co.kr`).
- A link the researcher labelled `official` on a domain we don't know (a museum's or an operator's own site) counts,
  but raises a warning Issue so a person confirms it before approving.
- News, blogs, SNS, resellers, travel agencies and wikis never count, whatever the label.
- Source material counts when its quoted words are in it (ADR 0003).
- The link must also pass the link check. A point with no fact that counts is left off the Deck; fewer than three
  points left fails the Job with a way forward (attach the official page as Source material).

## Consequences

- The allowlist needs upkeep: an operator's site that keeps coming up as "claimed" should be added to it.
- The cards show when the facts were checked (the time of Verify), and each point slide names the official page it
  rests on, so readers can check for themselves.
- Strict allowlist only, and trusting the label with a blocklist, were the alternatives. The first rejects operators'
  own sites until someone edits the list; the second lets a mislabelled page carry a fact with nobody looking.
