# Contributing

Nemo K Cards needs reproducible improvements to event sourcing, card quality, setup and agent boundaries. Small focused contributions are easiest to review.

## Start locally

Follow [Getting started](docs/GETTING_STARTED.md) using fixture data and mock publishing. Do not use the project's Instagram account or someone else's credentials for development.

## Useful contributions

- Add an event-source fixture with an original URL, retrieval date and expected interpretation.
- Add a case with expired or conflicting event dates and explain the intended choice.
- Improve visual accessibility without changing the event's facts or removing attribution.
- Reproduce a setup failure with environment details and a minimal example.
- Add a policy regression case that distinguishes a model refusal from runtime enforcement.

## Pull requests

1. Open an issue first for broad behavior or architecture changes.
2. Make the change on a branch and keep unrelated generated files out of the commit.
3. Run the relevant checks below.
4. Describe the problem, resulting behavior and limitations. Include before/after screenshots for visual changes.
5. Confirm that copied photos, fonts and fixtures have appropriate rights and attribution.

```bash
cd backend
uv sync --frozen
uv run playwright install chromium
uv run pytest -q
```

For Admin changes, run `pnpm exec tsc --noEmit` and `pnpm lint` in `apps/web`. Follow [DESIGN.md](DESIGN.md) for UI and cards. For policy changes, include the effective policy, sandbox version and probe results from a compatible host.

Never commit `.env` files, access codes, tokens, runtime databases or unsanitized logs. Report security issues through [SECURITY.md](SECURITY.md).

By submitting a contribution, you agree that your original contribution is provided under the repository's MIT license. Third-party material must retain its own attribution and license.
