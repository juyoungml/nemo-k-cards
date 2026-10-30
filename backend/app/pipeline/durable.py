"""Durable Jobs (#17, ADR 0002): DBOS runs the pipeline as a workflow whose steps are checkpointed in Postgres.

Where things live
- DBOS keeps its checkpoints (workflow status, inputs, each step's output, the queue) in the `dbos` schema of
  the same database as the app data. DBOS creates and upgrades that schema itself at launch, so its DDL follows
  the installed DBOS version; `supabase/migrations/*_dbos_schema.sql` only pre-creates the schema closed to
  every API-facing role (`anon`, `authenticated`, `service_role`, `wok_api`, PUBLIC).
- DBOS connects as the connecting role, like `store.system()`: its tables span Organizations and are not RLS
  tables. App data stays behind RLS: every step reads and writes the Job through `store.tenant(job's org)`.

Process model
- DBOS runs inside the API process (`launch()` in the FastAPI lifespan). On launch it resumes every workflow this
  executor left PENDING, at the first unfinished step. Workflows run on DBOS's own event loop thread, so a long
  stage never competes with request handling on uvicorn's loop.
- To scale later (#28), run a separate worker process with the same code that calls `launch()`, and have the API
  call `launch(listen=False)` so it only enqueues. Both share the database. Each process then needs a stable
  `DBOS__VMID` (executor id), so a restarted process recovers its own work.

Queues
- `jobs`, partitioned by Organization: `JOBS_PER_ORG` caps each Organization's running Jobs, `JOBS_PER_WORKER`
  caps one process. A Job waiting for its Organization's slot stays QUEUED.
- `deck_checks`: the Safeguard re-check of an edited Deck version (#19), one short workflow per version.

Schedules
- `instagram-token-refresh`: every day at 03:17 KST (#22, app/token_refresh.py).
"""

import logging
import threading

from dbos import DBOS, DBOSConfig, DBOSPortableJSONSerializer
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

from app.config import settings

log = logging.getLogger("durable")
APP_NAME = "whatsonkorea"
SCHEMA = "dbos"
JOB_QUEUE = "jobs"
DECK_CHECK_QUEUE = "deck_checks"  # the Safeguard re-check after a Deck edit (#19, app/deck_versions.py)
DECK_CHECKS_PER_WORKER = 4
QUEUE_POLL_S = 1.0
_lock = threading.Lock()
_launched = False


def _url(conninfo: str) -> str:
    """DBOS wants a URL; DATABASE_URL may also be a libpq key=value string (psycopg takes both)."""
    if "://" in conninfo:
        return conninfo
    d = conninfo_to_dict(conninfo)
    named = {k: d.pop(k, None) for k in ("user", "password", "host", "port", "dbname")}
    return URL.create("postgresql", username=named["user"], password=named["password"], host=named["host"],
                      port=int(named["port"]) if named["port"] else None, database=named["dbname"],
                      query={k: str(v) for k, v in d.items()}).render_as_string(hide_password=False)


def config() -> DBOSConfig:
    return {
        "name": APP_NAME,
        "system_database_url": _url(settings.dbos_database_url or settings.database_url),
        "dbos_system_schema": SCHEMA,
        "application_version": settings.workflow_version,
        "sys_db_pool_size": 10,
        # prepare_threshold=None: also works behind Supabase's transaction pooler (Supavisor), as the store does.
        "db_engine_kwargs": {"connect_args": {"application_name": "wok_dbos", "prepare_threshold": None}},
        # Polling instead of LISTEN/NOTIFY: a transaction pooler drops LISTEN sessions, and nothing here waits on
        # DBOS notifications (progress goes through the Job document, as before).
        "use_listen_notify": False,
        # Checkpoints as plain JSON, not pickle: readable in SQL, and loading one never runs code.
        "serializer": DBOSPortableJSONSerializer(),
        "max_executor_threads": 64,
    }


def launched() -> bool:
    return _launched


def launch(*, listen: bool = True) -> None:
    """Start DBOS (idempotent): migrate its schema, register the Job queue, resume interrupted workflows.
    Call it from a thread without a running event loop (`asyncio.to_thread`), so workflows get DBOS's own loop.
    The pipeline module must be imported first: its workflow and steps register at import."""
    global _launched
    from app import (
        deck_versions,  # noqa: F401  (registers the Deck re-check workflow, #19)
        token_refresh,  # registers the daily token refresh workflow (#22)
    )
    from app.pipeline import (  # noqa: F401  (registers the workflows before launch)
        orchestrator,
        publishing,
    )

    with _lock:
        if _launched:
            return
        DBOS(config=config())
        if not listen:
            DBOS.listen_queues([])
        DBOS.launch()
        register_queue()
        publishing.register_queue()
        token_refresh.register_schedule()
        _launched = True


def register_queue(*, per_org: int | None = None, per_worker: int | None = None,
                   poll_s: float = QUEUE_POLL_S) -> None:
    DBOS.register_queue(JOB_QUEUE, partition_concurrency=per_org or settings.jobs_per_org,
                        worker_concurrency=per_worker or settings.jobs_per_worker, polling_interval_sec=poll_s,
                        on_conflict="always_update")
    # Not behind the Job queue's per-Organization slot: a re-check takes seconds and the Deck can't be approved
    # until it finishes, so it must not wait for that Organization's running pipelines.
    DBOS.register_queue(DECK_CHECK_QUEUE, worker_concurrency=DECK_CHECKS_PER_WORKER, polling_interval_sec=poll_s,
                        on_conflict="always_update")


def shutdown() -> None:
    """Stop DBOS without waiting: running workflows stay PENDING and resume at the next launch."""
    global _launched
    with _lock:
        if _launched:
            DBOS.destroy(workflow_completion_timeout_sec=0)
            _launched = False
