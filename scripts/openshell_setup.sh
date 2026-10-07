#!/usr/bin/env bash
# One-time OpenShell setup for AGENT_RUNNER=openshell (BACKEND §6, §0.3).
#
#   ANTHROPIC_API_KEY=... scripts/openshell_setup.sh
#   CLAUDE_CODE_OAUTH_TOKEN=... scripts/openshell_setup.sh    # Claude subscription (`claude setup-token`)
#   scripts/openshell_setup.sh                                # or either one set in backend/.env
#
# 1. builds the agent image (infra/sandbox/Dockerfile)
# 2. imports the claude-code provider profile (policies/openshell/providers/claude-code.yaml)
# 3. creates the `claude-code` provider from ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN — the key lives in the gateway; sandboxes
#    only ever see a placeholder, and it is sent to api.anthropic.com only.
#
# Gateway: on hosts with Linux < 6.2 or Docker < 28 (e.g. Ubuntu 22.04), run sandboxes as microVMs:
#   ~/.config/openshell/gateway.toml →  [openshell] version = 2
#                                       [openshell.gateway] compute_driver = "vm"
#   and add the gateway user to the `kvm` group.
set -euo pipefail
cd "$(dirname "$0")/.."

# The common-test folders are baked into the image (/hackathon); HACKATHON_DIR points at them.
HACKATHON_DIR=${HACKATHON_DIR:-../k-culture-openshell-challenge/hackathon}
for d in input output restricted secrets; do
  [ -d "$HACKATHON_DIR/$d" ] || { echo "HACKATHON_DIR=$HACKATHON_DIR has no $d/ (set HACKATHON_DIR)" >&2; exit 1; }
done
docker build --build-context hackathon="$HACKATHON_DIR" -f infra/sandbox/Dockerfile -t whatsonkorea-agent:latest .

profile=policies/openshell/providers/claude-code.yaml
openshell profile lint -f "$profile"
if openshell profile list 2>/dev/null | grep -qw claude-code; then
  openshell profile update -f "$profile"
else
  openshell profile import -f "$profile"
fi

# Fall back to backend/.env (gitignored) for the credential; only these two keys are read from it.
if [ -z "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -f backend/.env ]; then
  for k in ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN; do
    v=$(sed -n "s/^${k}=//p" backend/.env | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//')
    [ -n "$v" ] && export "$k=$v"
  done
fi
if [ -z "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN to create the provider" >&2
  exit 1
fi
if openshell provider get claude-code >/dev/null 2>&1; then
  openshell provider update claude-code --from-existing
else
  openshell provider create --name claude-code --type claude-code --from-existing
fi
openshell provider get claude-code
