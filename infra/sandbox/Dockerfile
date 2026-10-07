# Agent sandbox image for OpenShell (SPEC §3). Build from repo root:
#   docker build -f infra/sandbox/Dockerfile -t whatsonkorea-agent:latest .
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl nodejs npm python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && ln -sf "$(npm root -g)/@anthropic-ai/claude-code/cli.js" /usr/local/bin/claude

RUN useradd -m sandbox && mkdir -p /sandbox/agent/policies /sandbox/input /sandbox/out \
    && chown -R sandbox:sandbox /sandbox/out
COPY agent/ /sandbox/agent/
COPY policies/content/ /sandbox/agent/policies/

USER sandbox
WORKDIR /sandbox/agent
