"""Run the golden set and print a pass/fail report. Exit code 1 if any case fails.

    uv run python -m app.golden                         # replay every recorded case (free, deterministic)
    uv run python -m app.golden -v --case seongsu-popups # one case, every check
    uv run python -m app.golden --strict                # stale recordings (prompts / model / set changed) fail
    uv run python -m app.golden --live --case seongsu-popups [--record]   # real pipeline, costs tokens

Live runs write slides and their SQLite under --out (default backend/.data/golden), never into the Admin's data,
and never publish. --record overwrites backend/golden/recordings/<case>.json with the run.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ICON = {"pass": "✓", "fail": "✗", "warn": "!", "skip": "-"}


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m app.golden", description=__doc__.split("\n")[0])
    ap.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ap.add_argument("--live", action="store_true", help="run the real pipeline (claude -p, costs tokens)")
    ap.add_argument("--record", action="store_true", help="with --live: save each run as the case's recording")
    ap.add_argument("--out", type=Path, help="with --live: where slides and the job DB go")
    ap.add_argument("--strict", action="store_true", help="stale recordings count as failures")
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    ap.add_argument("-v", "--verbose", action="store_true", help="show passing checks too")
    a = ap.parse_args()
    if a.record and not a.live:
        ap.error("--record needs --live")
    return a


def _isolate_live(out: Path | None) -> Path:
    """Before app.config is imported: live agents, mock publishing, no publishing credentials, scratch data dir."""
    out = (out or Path(__file__).resolve().parents[2] / ".data/golden").expanduser().resolve()
    os.environ.update({"DEMO_MODE": "live", "PUBLISH_MODE": "mock", "IMAGE_HOST": "local",
                       "OUTPUT_DIR": str(out / "out"), "IG_USER_ID": "", "IG_ACCESS_TOKEN": "",
                       "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""})
    return out


def main() -> int:
    a = _args()
    out = None
    if a.live:
        if "app.config" in sys.modules:
            raise RuntimeError("live mode must set its environment before app.config is imported")
        out = _isolate_live(a.out)

    from app.config import settings  # imported only now: live mode set the environment above
    from app.golden.cases import (
        load_recording,
        load_set,
        prompts_sha,
        save_recording,
    )
    from app.golden.checks import run_checks

    gs = load_set()
    cases = [c for c in gs.cases if not a.case or c.id in a.case]
    if a.case and (unknown := set(a.case) - {c.id for c in cases}):
        print(f"unknown case(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    sha = prompts_sha()
    mode = "live" if a.live else "replay"
    print(f"Golden set v{gs.version} · {mode} · {len(cases)} case(s) · agent prompts {sha}"
          + (f" · model {settings.agent_model or 'CLI default'}" if a.live else ""))
    if a.live:
        print(f"  live output → {out} (publish mode {settings.publish_mode}, nothing is posted)")

    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    report, totals = [], {"pass": 0, "fail": 0, "not recorded": 0, "stale": 0}
    for case in cases:
        if a.live:
            from app.golden.live import run_case
            print(f"\n… running {case.id}: {case.prompt!r}", flush=True)
            try:
                rec = asyncio.run(run_case(case, gs.version))
            except ValueError as e:
                print(f"{case.id:<26} SKIP  {e}")
                continue
            path = save_recording(rec) if a.record else save_recording(rec, out / "recordings")
            print(f"  recording → {path}")
        else:
            rec = load_recording(case.id)
        if rec is None:
            totals["not recorded"] += 1
            print(f"{case.id:<26} ----  not recorded (uv run python -m app.golden --live --case {case.id} --record)")
            report.append({"case": case.id, "status": "not recorded"})
            continue

        stale = []
        if rec.set_version != gs.version:
            stale.append(f"recorded for set v{rec.set_version}")
        if rec.provenance == "live" and rec.prompts_sha != sha:
            stale.append("agent prompts changed since recording")
        if rec.provenance == "live" and rec.model != settings.agent_model:
            stale.append(f"recorded with model {rec.model or 'CLI default'}")
        results = run_checks(case, rec)
        failed = any(r.status == "fail" for r in results) or (a.strict and bool(stale))
        totals["fail" if failed else "pass"] += 1
        totals["stale"] += bool(stale)
        origin = f"{rec.provenance} · recorded {rec.recorded_at:%Y-%m-%d}"
        print(f"{case.id:<26} {'FAIL' if failed else 'PASS'}  {origin}" + (f" · STALE: {', '.join(stale)}"
                                                                         if stale else ""))
        for r in results:
            if a.verbose or r.status in ("fail", "warn"):
                tag = " [llm]" if r.judge == "llm" else ""
                print(f"    {ICON[r.status]} {r.name}{tag}: {r.detail}")
                if gha and r.status == "fail":
                    print(f"::error title=golden {case.id} · {r.name}::{r.detail[:500]}")
        if gha and stale:
            print(f"::warning title=golden {case.id} is stale::{', '.join(stale)}. Re-record: "
                  f"cd backend && uv run python -m app.golden --live --case {case.id} --record")
        report.append({"case": case.id, "status": "fail" if failed else "pass", "stale": stale,
                       "provenance": rec.provenance, "recorded_at": rec.recorded_at.isoformat(),
                       "checks": [asdict(r) for r in results]})

    print("\n" + " · ".join(f"{n} {k}" for k, n in totals.items()))
    if a.json:
        a.json.write_text(json.dumps({"version": gs.version, "mode": mode, "prompts_sha": sha, "totals": totals,
                                      "cases": report}, indent=2, ensure_ascii=False))
    return 1 if totals["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
