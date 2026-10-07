# Security

Nemo K Cards is an early project. OpenShell policies constrain the agent runtime, but they do not establish the safety of the entire application or the factual correctness of generated content.

## Reporting a vulnerability

Use [GitHub's private vulnerability report](https://github.com/juyoungml/nemo-k-cards/security/advisories/new) for credential exposure, authorization bypasses, sandbox escapes or unauthorized publishing. Do not put working access codes, tokens or exploit details in public issues.

Include the affected commit, execution mode, OpenShell version/driver, a minimal reproduction and redacted logs. There is no guaranteed response SLA.

## Deployment boundaries

- Keep Instagram and image-hosting credentials on the backend.
- Start with `PUBLISH_MODE=mock`; `graph` can publish publicly after approval.
- Use a strong `ADMIN_TOKEN` and explicit `CORS_ORIGINS` for an externally reachable backend.
- The shared-code gate is not a multi-user permission system. Do not expose a shared live-publishing account as an unrestricted public demo.
- `AGENT_RUNNER=local` is not sandboxed. Fixture or UI-mock results do not prove OpenShell enforcement.
- Editorial warnings are mostly advisory. Review them before approving; credential-like output is a hard stop.
- Use only trusted contributors for configuration changes and re-run policy probes after changes.

See [setup details](docs/GETTING_STARTED.md) and the [checked-in policy](policies/openshell/agent-policy.yaml).
