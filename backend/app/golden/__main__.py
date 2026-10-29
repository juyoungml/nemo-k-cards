"""Run the golden set and print a pass/fail report. Exit code 1 if any case fails.

    uv run python -m app.golden                         # replay every recorded case (free, deterministic)
    uv run python -m app.golden -v --case seongsu-popups # one case, every check
    uv run python -m app.golden --strict                # stale recordings (agent / model / set changed) fail
    uv run python -m app.golden --live --case seongsu-popups [--record]   # real pipeline, costs tokens

Live runs never publish and never touch the Admin's data: slides go under --out (default backend/.data/golden);
with the SQLite store its database sits there too, with the Postgres store (settings.database_url) the run uses
GOLDEN_DATABASE_URL if it names a golden/test database, or else creates a scratch database on TEST_DATABASE_URL's
server (default: local Supabase), applies supabase/migrations and drops it afterwards.
--record overwrites backend/golden/recordings/<case>.json with the run.
"""

import argparse
import asyncio
import atexit
import json
import os
import secrets
import sys
from dataclasses import asdict
from pathlib import Path

ICON = {"pass": "✓", "fail": "✗", "warn": "!", "skip": "-"}
REPO = Path(__file__).resolve().parents[3]
LOCAL_SUPABASE = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m app.golden", description=__doc__.split("\n")[0])
    ap.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ap.add_argument("--live", action="store_true", help="run the real pipeline (claude -p, costs tokens)")
    ap.add_argument("--record", action="store_true", help="with --live: save each run as the case's recording")
    ap.add_argument("--out", type=Path, help="with --live: where slides (and a SQLite store) go")
    ap.add_argument("--model", help="expected model id for replay staleness (default: `model` in cases.json)")
    ap.add_argument("--strict", action="store_true", help="stale recordings count as failures")
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    ap.add_argument("-v", "--verbose", action="store_true", help="show passing checks too")
    a = ap.parse_args()
    if a.record and not a.live:
        ap.error("--record needs --live")
    return a


def _scratch_database() -> str:
    """A throwaway Postgres database with the migrations applied, like the test suite's (dropped at exit)."""
    import psycopg  # only present once the store is Postgres

    server = os.environ.get("TEST_DATABASE_URL", LOCAL_SUPABASE)
    name = f"wok_golden_{secrets.token_hex(4)}"
    with psycopg.connect(server, autocommit=True) as c:
        c.execute(f"create database {name}")
    url = server.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(url) as c:
        for sql in sorted((REPO / "supabase/migrations").glob("*.sql")):
            c.execute(sql.read_text())

    def drop() -> None:
        with psycopg.connect(server, autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
    atexit.register(drop)
    return url


def _isolate_live(out: Path | None) -> Path:
    """Before app.config is imported: live agents, mock publishing, no publishing credentials, scratch data."""
    out = (out or REPO / "backend/.data/golden").expanduser().resolve()
    os.environ.update({"DEMO_MODE": "live", "PUBLISH_MODE": "mock", "IMAGE_HOST": "local",
                       "OUTPUT_DIR": str(out / "out"), "IG_USER_ID": "", "IG_ACCESS_TOKEN": "",
                       "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""})
    from app.config import settings
    if hasattr(settings, "database_url"):  # Postgres store: never the configured (possibly live) database
        from app.golden.live import SCRATCH_DB, db_name
        url = os.environ.get("GOLDEN_DATABASE_URL", "")
        if url and not SCRATCH_DB.search(db_name(url)):
            sys.exit(f"GOLDEN_DATABASE_URL must name a golden/test database, not '{db_name(url)}'")
        url = url or _scratch_database()
        os.environ["DATABASE_URL"] = settings.database_url = url  # the store connects lazily, on first use
    return out


def main() -> int:
    a = _args()
    out = None
    if a.live:
        if "app.config" in sys.modules:
            raise RuntimeError("live mode must set its environment before app.config is imported")
        out = _isolate_live(a.out)

    from app.config import settings  # imported only now: live mode set the environment above
    from app.golden.cases import agent_sha, load_recording, load_set, save_recording
    from app.golden.checks import run_checks

    gs = load_set()
    cases = [c for c in gs.cases if not a.case or c.id in a.case]
    if a.case and (unknown := set(a.case) - {c.id for c in cases}):
        print(f"unknown case(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    sha, pinned = agent_sha(), a.model or gs.model
    mode = "live" if a.live else "replay"
    print(f"Golden set v{gs.version} · {mode} · {len(cases)} case(s) · agent {sha} · pinned model {pinned or '-'}")
    if a.live:
        db = getattr(settings, "database_url", "") or "SQLite next to it"
        print(f"  live output → {out} · db {db.rsplit('/', 1)[-1]} · publish mode {settings.publish_mode} "
              f"(nothing is posted)")

    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    report, totals = [], {"pass": 0, "fail": 0, "fixture": 0, "not recorded": 0, "stale": 0}
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
            print(f"{case.id:<26} ----    not recorded (uv run python -m app.golden --live --case {case.id} --record)")
            report.append({"case": case.id, "status": "not recorded"})
            continue

        live = rec.provenance == "live"
        stale = []
        if rec.set_version != gs.version:
            stale.append(f"recorded for set v{rec.set_version}")
        if live and rec.agent_sha != sha:
            stale.append("agent prompts, stage tasks or output schemas changed since recording")
        if live and pinned and rec.model != pinned:
            stale.append(f"recorded with model {rec.model or 'unknown'}, pinned {pinned}")
        results = run_checks(case, rec)
        failed = any(r.status == "fail" for r in results) or (a.strict and bool(stale))
        # Fixture recordings (demo-scenario, synthetic) guard the checks themselves, not the agents: they never
        # count as a passing case.
        status = "fail" if failed else ("pass" if live else "fixture")
        totals[status] += 1
        totals["stale"] += bool(stale)
        origin = f"{rec.provenance} · recorded {rec.recorded_at:%Y-%m-%d}" + (
            f" · {rec.model or 'model unknown'} · cli {rec.cli_version or '?'}" if live else "")
        print(f"{case.id:<26} {status.upper():<7} {origin}" + (f" · STALE: {', '.join(stale)}" if stale else ""))
        for r in results:
            if a.verbose or r.status in ("fail", "warn"):
                tag = " [llm]" if r.judge == "llm" else ""
                print(f"    {ICON[r.status]} {r.name}{tag}: {r.detail}")
                if gha and r.status == "fail":
                    print(f"::error title=golden {case.id} · {r.name}::{r.detail[:500]}")
        if gha and stale:
            print(f"::warning title=golden {case.id} is stale::{', '.join(stale)}. Re-record: "
                  f"cd backend && uv run python -m app.golden --live --case {case.id} --record")
        report.append({"case": case.id, "status": status, "stale": stale, "provenance": rec.provenance,
                       "model": rec.model, "cli_version": rec.cli_version,
                       "recorded_at": rec.recorded_at.isoformat(), "checks": [asdict(r) for r in results]})

    print("\n" + " · ".join(f"{n} {k}" for k, n in totals.items()))
    if a.json:
        a.json.write_text(json.dumps({"version": gs.version, "mode": mode, "agent_sha": sha, "pinned_model": pinned,
                                      "totals": totals, "cases": report}, indent=2, ensure_ascii=False))
    return 1 if totals["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
