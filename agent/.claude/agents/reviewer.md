---
name: reviewer
description: Final gate before human review — checks facts, links, sensitivity, PII, and slide images. Outputs ReviewVerdict JSON.
tools: Read
---

<!-- TODO: write the reviewer prompt. Role & checks: docs/SPEC.md §4, §7. -->

Output ONLY JSON matching `ReviewVerdict` in backend/app/schemas.py.
