#!/usr/bin/env bash
# One-time OpenShell setup for AGENT_RUNNER=openshell (BACKEND §6, §0.3).
#
#   ANTHROPIC_API_KEY=... scripts/openshell_setup.sh
#
# 1. builds the agent image (infra/sandbox/Dockerfile)
# 2. imports the claude-code provider profile (policies/openshell/providers/claude-code.yaml)
# 3. creates the `claude-code` provider from ANTHROPIC_API_KEY — the key lives in the gateway; sandboxes
#    only ever see a placeholder, and it is sent to api.anthropic.com only.
#
# Gateway: on hosts with Linux < 6.2 or Docker < 28 (e.g. Ubuntu 22.04), run sandboxes as microVMs:
#   ~/.config/openshell/gateway.toml →  [openshell] version = 2
#                                       [openshell.gateway] compute_driver = "vm"
#   and add the gateway user to the `kvm` group.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -f infra/sandbox/Dockerfile -t whatsonkorea-agent:latest .

profile=policies/openshell/providers/claude-code.yaml
openshell profile lint -f "$profile"
if openshell profile list 2>/dev/null | grep -qw claude-code; then
  openshell profile update -f "$profile"
else
  openshell profile import -f "$profile"
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is not set; skipping provider creation" >&2
  exit 1
fi
if openshell provider get claude-code >/dev/null 2>&1; then
  openshell provider update claude-code --from-existing
else
  openshell provider create --name claude-code --type claude-code --from-existing
fi
openshell provider get claude-code
