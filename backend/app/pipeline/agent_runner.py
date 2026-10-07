"""Runs a Claude Code subagent for one pipeline stage and returns validated JSON (SPEC §3).

One interface, two runners: start with the local CLI, switch to OpenShell (or the Agent SDK)
without touching the orchestrator.
"""

from typing import Protocol

from app.config import settings


class AgentRunner(Protocol):
    async def run(self, agent: str, task: str, payload: dict) -> str: ...


class LocalClaudeRunner:
    """Dev only: `claude -p ... --output-format json` on the host, cwd=agent/. No sandbox."""

    async def run(self, agent: str, task: str, payload: dict) -> str:
        raise NotImplementedError


class OpenShellRunner:
    """`openshell sandbox create --policy ... --provider claude-code --no-keep -- claude -p ...`"""

    async def run(self, agent: str, task: str, payload: dict) -> str:
        raise NotImplementedError


def get_runner() -> AgentRunner:
    return OpenShellRunner() if settings.agent_runner == "openshell" else LocalClaudeRunner()


async def run_stage[T](agent: str, task: str, payload: dict, out_type: type[T]) -> T:
    raise NotImplementedError  # TODO: get_runner().run(...) -> TypeAdapter(out_type).validate_json
