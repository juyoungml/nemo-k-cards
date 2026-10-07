# Agent sandbox image for OpenShell (SPEC §3). Build from repo root (scripts/openshell_setup.sh does this):
#   docker build --build-context hackathon=<challenge>/hackathon -f infra/sandbox/Dockerfile -t whatsonkorea-agent:latest .
FROM ubuntu:24.04

# Claude Code ships as a native binary; npm is only the installer. The binary is copied to a real path
# because OpenShell matches network rules on an executable's real path (not symlinks) and pins its hash.
ARG CLAUDE_CODE_VERSION=2.1.292
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl npm \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && cp "$(npm root -g)/@anthropic-ai/claude-code/bin/claude.exe" /tmp/claude \
    && npm uninstall -g @anthropic-ai/claude-code \
    && install -m 0755 /tmp/claude /usr/local/bin/claude && rm /tmp/claude \
    && apt-get purge -y npm && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /root/.npm

# The pinned binary must not replace itself (hash pinning would deny it), and only api.anthropic.com
# is allowed out, so telemetry / error reporting stay off.
# OpenShell sets HOME=/sandbox, which the policy keeps read-only; Claude Code's session state goes to /tmp.
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CONFIG_DIR=/tmp/claude

# OpenShell runs the workload as the image's `sandbox` account (uid 1000 unless the image says otherwise);
# ubuntu:24.04 ships uid 1000 as `ubuntu`, so give that uid to `sandbox`.
# The Docker driver refuses a workdir the sandbox user cannot write, so `sandbox` owns /sandbox/agent;
# the policy's Landlock rules keep it read-only at runtime (scripts/openshell_probe.sh checks this).
RUN userdel -r ubuntu && useradd -m -u 1000 sandbox && mkdir -p /sandbox/agent/policies /sandbox/input /sandbox/out \
    && chown -R sandbox:sandbox /sandbox/agent /sandbox/out /sandbox/input
COPY --chown=sandbox:sandbox agent/ /sandbox/agent/
COPY --chown=sandbox:sandbox policies/content/ /sandbox/agent/policies/

# Common-test folders (k-culture-openshell-challenge/hackathon), passed as a build context:
#   docker build --build-context hackathon=<challenge>/hackathon -f infra/sandbox/Dockerfile ...
# Access is set by policies/openshell/agent-policy.yaml (Landlock): input read-only, output read-write,
# restricted and secrets not listed, so not reachable at all. The modes below are the second layer: even
# without Landlock the sandbox user (uid 1000) cannot read restricted/ or secrets/ (root-only, 0700).
COPY --from=hackathon input/ /hackathon/input/
COPY --from=hackathon restricted/ /hackathon/restricted/
COPY --from=hackathon secrets/ /hackathon/secrets/
RUN mkdir -p /hackathon/output && chown sandbox:sandbox /hackathon/output \
    && chown -R root:root /hackathon/input /hackathon/restricted /hackathon/secrets \
    && chmod -R a+rX,go-w /hackathon/input \
    && chmod 0700 /hackathon/restricted /hackathon/secrets \
    && chmod 0600 /hackathon/restricted/* /hackathon/secrets/*

USER sandbox
WORKDIR /sandbox/agent
