"""Runs a Claude Code subagent for one pipeline stage and returns validated JSON (SPEC §3, BACKEND §6).

One interface, two runners: start with the local CLI, switch to OpenShell without touching the orchestrator.
Output is constrained with `--json-schema` (the CLI returns it as `structured_output`) and validated again
with pydantic on the host.
"""

import asyncio
import json
import logging
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import TypeAdapter, ValidationError

from app import store
from app.config import settings
from app.services import policy_log

log = logging.getLogger("agent_runner")

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
    async def run(self, agent: str, task: str, payload: dict, schema: dict,
                  files: list[Path], job_id: str | None) -> Any: ...


def _cli_args(agent: str, task: str, schema: dict, add_dirs: list[str] = ()) -> list[str]:
    args = ["-p", task, "--agent", agent, "--output-format", "json",
            "--json-schema", json.dumps(schema), "--allowedTools", *TOOLS.get(agent, ["Read"])]
    for d in add_dirs:
        args += ["--add-dir", d]
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

    async def run(self, agent: str, task: str, payload: dict, schema: dict,
                  files: list[Path], job_id: str | None) -> Any:
        claude = shutil.which("claude")
        if not claude:
            raise AgentError("claude CLI not found on PATH")
        dirs = sorted({str(f.parent) for f in files})
        proc = await asyncio.create_subprocess_exec(
            claude, *_cli_args(agent, task, schema, dirs), cwd=settings.agent_dir,
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
    """One fresh OpenShell sandbox per stage (BACKEND §6):

    create --detach → upload input files → exec `claude -p` (payload on stdin) → collect the sandbox's
    policy log → delete. The sandbox is kept until its log is read (a `--no-keep` sandbox takes its log
    with it). Uploads land in /tmp/input because the policy keeps /sandbox/input read-only even for uploads.
    """

    SANDBOX_INPUT = "/tmp/input"
    # Image ENV doesn't reach VM sandboxes, so the CLI settings the image relies on are passed per exec.
    # HOME: the Docker driver sets HOME to the workdir, and Claude Code ignores a project's .claude/agents
    # when the project is $HOME.
    CLAUDE_ENV: ClassVar[dict[str, str]] = {"HOME": "/sandbox", "CLAUDE_CONFIG_DIR": "/tmp/claude",
                  "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

    def __init__(self) -> None:
        self.openshell = shutil.which("openshell") or ""

    async def _cli(self, *args: str, stdin: bytes | None = None, timeout: float = 120) -> tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            self.openshell, *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out, err

    async def _create(self, stage: str, job_id: str | None, providers: list[str]) -> str:
        if not self.openshell:
            raise AgentError("openshell CLI not found — install OpenShell or use AGENT_RUNNER=local")
        # OpenShell caps sandbox names at 19 chars: e.g. "revi-0bb413-a1b2".
        job = re.sub(r"[^a-z0-9]", "", (job_id or "adhoc").lower())[-8:]
        name = f"{stage[:4]}-{job}-{secrets.token_hex(2)}"
        rc, _, err = await self._cli(
            "sandbox", "create", "--name", name, "--detach", "--no-tty", "--from", settings.openshell_image,
            "--policy", str(settings.openshell_policy), *[a for p in providers for a in ("--provider", p)],
            "--label", f"stage={stage}", *(["--label", f"job={job_id}"] if job_id else []), timeout=300)
        if rc != 0:
            await self._delete(name)
            raise AgentError(f"sandbox create failed: {err.decode()[-300:]}")
        return name

    async def run(self, agent: str, task: str, payload: dict, schema: dict,
                  files: list[Path], job_id: str | None) -> Any:
        name = await self._create(agent, job_id, settings.openshell_providers)
        try:
            remap = {}
            for d in sorted({f.parent for f in files}):
                rc, _, err = await self._cli("sandbox", "upload", name, str(d), self.SANDBOX_INPUT)
                if rc != 0:
                    raise AgentError(f"upload to sandbox failed: {err.decode()[-300:]}")
                remap.update({str(f): f"{self.SANDBOX_INPUT}/{d.name}/{f.name}" for f in files if f.parent == d})
            env = [a for k, v in self.CLAUDE_ENV.items() for a in ("--env", f"{k}={v}")]
            dirs = [self.SANDBOX_INPUT] if files else []
            try:
                rc, stdout, err = await self._cli(
                    "sandbox", "exec", "-n", name, "--workdir", "/sandbox/agent", "--no-tty", *env,
                    "--", "claude", *_cli_args(agent, task, schema, dirs),
                    stdin=json.dumps(_remap(payload, remap), ensure_ascii=False, default=str).encode(),
                    timeout=settings.agent_timeout_s)
            except TimeoutError as e:
                raise AgentError(f"{agent} timed out in sandbox after {settings.agent_timeout_s}s") from e
            if rc != 0 and not stdout:
                raise AgentError(f"sandbox exec exited {rc}: {err.decode()[-300:]}")
            return _parse(stdout)
        finally:
            await self._collect_policy_events(name, job_id)
            await self._delete(name)

    async def run_script(self, stage: str, script: str, job_id: str | None,
                         note: str | None = None) -> tuple[str, str]:
        """Run a shell script (no agent, no providers) in a fresh sandbox under the same policy.

        Returns (sandbox name, stdout); the sandbox's policy events are stored like a stage's, with `note`
        prefixed to each row's note.
        """
        name = await self._create(stage, job_id, [])
        try:
            _, stdout, _ = await self._cli("sandbox", "exec", "-n", name, "--workdir", "/sandbox/agent", "--no-tty",
                                           "--", "sh", "-c", script, timeout=120)
            return name, stdout.decode(errors="replace")
        finally:
            await self._collect_policy_events(name, job_id, note)
            await self._delete(name)

    async def _collect_policy_events(self, name: str, job_id: str | None, note: str | None = None) -> None:
        # The sandbox ships its log to the gateway asynchronously; a read right after the workload exits
        # misses the last events, so re-read until the log stops growing.
        try:
            out, rc = b"", 1
            for _ in range(10):
                prev = out
                rc, out, _ = await self._cli("logs", name, "--source", "sandbox", "-n", "5000", "--color", "never")
                if rc != 0 or (out and out == prev):
                    break
                await asyncio.sleep(1)
            if rc == 0 and (events := policy_log.parse(out.decode(errors="replace"), name, job_id)):
                if note:
                    for e in events:
                        e.note = f"{note} · {e.note}" if e.note else note
                store.add_policy_events(events)
        except Exception:  # a missing log must not fail the stage
            log.exception("could not collect policy log for %s", name)

    async def _delete(self, name: str) -> None:
        try:
            await self._cli("sandbox", "delete", name)
        except Exception:
            log.exception("could not delete sandbox %s", name)


def _remap(value: Any, paths: dict[str, str]) -> Any:
    """Swap host file paths in the payload for where they were uploaded in the sandbox."""
    if isinstance(value, str):
        return paths.get(value, value)
    if isinstance(value, list):
        return [_remap(v, paths) for v in value]
    if isinstance(value, dict):
        return {k: _remap(v, paths) for k, v in value.items()}
    return value


def get_runner() -> AgentRunner:
    return OpenShellRunner() if settings.agent_runner == "openshell" else LocalClaudeRunner()


async def run_stage[T](agent: str, task: str, payload: dict, out_type: type[T], *,
                       files: list[Path] = (), job_id: str | None = None) -> T:
    """Run one subagent; retry once with the validation error appended (BACKEND §6).

    `files` are host files the agent must read (rendered slides); `job_id` tags the sandbox and its
    policy events.
    """
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
            raw = await runner.run(agent, t, payload, schema, list(files), job_id)
            if wrap and isinstance(raw, list):  # agent returned the bare list
                raw = {"items": raw}
            return adapter.validate_python(raw["items"] if wrap else raw)
        except (ValidationError, KeyError, TypeError, AgentOutputError) as e:
            last_err = str(e)[:1500]
    raise AgentError(f"{agent}: output failed validation twice: {last_err}")
