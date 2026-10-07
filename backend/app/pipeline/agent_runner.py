"""Runs a Claude Code subagent for one pipeline stage and returns validated JSON (SPEC §3, BACKEND §6).

One interface, two runners: start with the local CLI, switch to OpenShell without touching the orchestrator.
Output is constrained with `--json-schema` (the CLI returns it as `structured_output`) and validated again
with pydantic on the host.
"""

import asyncio
import json
import re
import shutil
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from app.config import settings

# Tools each subagent may use inside its session (the OpenShell policy is the real boundary).
TOOLS = {
    "researcher": ["WebSearch", "WebFetch", "Read"],
    "planner": ["Read"],
    "copywriter": ["Read"],
    "reviewer": ["Read"],
}


class AgentError(RuntimeError):
    pass


class AgentOutputError(AgentError):
    """The agent ran but its output wasn't usable JSON — worth one retry."""


class AgentRunner(Protocol):
    async def run(self, agent: str, task: str, payload: dict, schema: dict) -> Any: ...


def _cli_args(agent: str, task: str, schema: dict) -> list[str]:
    args = ["-p", task, "--agent", agent, "--output-format", "json",
            "--json-schema", json.dumps(schema), "--allowedTools", *TOOLS.get(agent, ["Read"])]
    if agent == "reviewer":
        args += ["--add-dir", str(settings.output_dir)]
    if settings.agent_model:
        args += ["--model", settings.agent_model]
    return args


def _extract_json(text: str) -> Any:
    """Accept bare JSON, fenced JSON, or JSON embedded in prose."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
        if starts:
            start = min(starts)
            end = max(text.rfind("}"), text.rfind("]"))
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise AgentOutputError(f"no JSON in agent output: {text[:200]!r}")


def _parse(stdout: bytes) -> Any:
    try:
        out = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"agent returned non-JSON: {stdout[:300]!r}") from e
    if out.get("is_error"):
        raise AgentError(f"agent error: {out.get('result') or out.get('subtype')}")
    if out.get("structured_output") is not None:
        return out["structured_output"]
    return _extract_json(out.get("result", ""))


class LocalClaudeRunner:
    """Dev only: `claude -p` on the host with cwd=agent/. No sandbox."""

    async def run(self, agent: str, task: str, payload: dict, schema: dict) -> Any:
        claude = shutil.which("claude")
        if not claude:
            raise AgentError("claude CLI not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            claude, *_cli_args(agent, task, schema), cwd=settings.agent_dir,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False, default=str).encode()),
                timeout=settings.agent_timeout_s)
        except TimeoutError as e:
            proc.kill()
            raise AgentError(f"{agent} timed out after {settings.agent_timeout_s}s") from e
        if proc.returncode != 0 and not stdout:
            raise AgentError(f"{agent} exited {proc.returncode}: {stderr.decode()[-300:]}")
        return _parse(stdout)


class OpenShellRunner:
    """`openshell sandbox create --policy ... --provider claude-code --no-keep -- claude -p ...`"""

    async def run(self, agent: str, task: str, payload: dict, schema: dict) -> Any:
        openshell = shutil.which("openshell")
        if not openshell:
            raise AgentError("openshell CLI not found — install OpenShell or use AGENT_RUNNER=local")
        cmd = [openshell, "sandbox", "create", "--from", settings.openshell_image,
               "--policy", str(settings.openshell_policy), "--no-keep",
               *[a for p in settings.openshell_providers for a in ("--provider", p)],
               "--", "claude", *_cli_args(agent, task, schema)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False, default=str).encode()),
                timeout=settings.agent_timeout_s)
        except TimeoutError as e:
            proc.kill()
            raise AgentError(f"{agent} timed out in sandbox") from e
        if proc.returncode != 0 and not stdout:
            raise AgentError(f"sandbox exited {proc.returncode}: {stderr.decode()[-300:]}")
        return _parse(stdout)


def get_runner() -> AgentRunner:
    return OpenShellRunner() if settings.agent_runner == "openshell" else LocalClaudeRunner()


async def run_stage[T](agent: str, task: str, payload: dict, out_type: type[T]) -> T:
    """Run one subagent; retry once with the validation error appended (BACKEND §6)."""
    adapter = TypeAdapter(out_type)
    schema = adapter.json_schema()
    if schema.get("type") != "object":  # --json-schema needs an object at the root
        defs = schema.pop("$defs", {})  # keep $refs resolvable from the new root
        schema = {"type": "object", "properties": {"items": schema}, "required": ["items"], "$defs": defs}
        wrap = True
    else:
        wrap = False
    # --json-schema is not always enforced for custom agents, so the contract also goes in the task text.
    task = (f"{task}\n\nReturn ONLY a JSON value that validates against this JSON Schema. Field names must match "
            f"exactly; no extra keys, no prose, no code fences.\n{json.dumps(schema, separators=(',', ':'))}")
    runner, last_err = get_runner(), None
    for attempt in range(2):
        t = task if attempt == 0 else f"{task}\n\nYour previous output failed validation:\n{last_err}\nFix it."
        try:
            raw = await runner.run(agent, t, payload, schema)
            if wrap and isinstance(raw, list):  # agent returned the bare list
                raw = {"items": raw}
            return adapter.validate_python(raw["items"] if wrap else raw)
        except (ValidationError, KeyError, TypeError, AgentOutputError) as e:
            last_err = str(e)[:1500]
    raise AgentError(f"{agent}: output failed validation twice: {last_err}")
