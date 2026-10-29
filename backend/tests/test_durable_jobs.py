"""Durable Jobs (#17): the pipeline is a DBOS workflow, one checkpointed step per stage.

- A crash mid-pipeline followed by a restart resumes at the interrupted stage; finished stages don't run again.
- Each Organization has a cap on Jobs running at once (a partitioned DBOS queue); one Organization's backlog
  doesn't hold up another's, and each workflow only ever touches its own Organization's Job.
- Fixture mode still runs end to end through the API (Quick and Brainstorm), now through the queue.
"""

import asyncio
import secrets
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from dbos import DBOS

from app import orgs, store
from app.pipeline import durable, orchestrator
from app.schemas import JobStatus, LinkCheck
from app.services import link_checker


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


@pytest.fixture
def no_network(monkeypatch):
    async def fake_check(url, client=None):
        return LinkCheck(url=url, status="ok", http_code=200)
    monkeypatch.setattr(link_checker, "check_url", fake_check)


async def wait_for(org_id, job_id, statuses=(JobStatus.READY_FOR_REVIEW, JobStatus.FAILED), timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = await asyncio.to_thread(store.get_job, org_id, job_id)
        if job and job.status in statuses:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} still {job.status if job else None} after {timeout}s")


def workflow_status(org_id, job_id) -> str | None:
    st = DBOS.get_workflow_status(orchestrator.workflow_id(org_id, job_id))
    return st.status if st else None


async def workflow_done(org_id, job_id, timeout=10.0) -> str | None:
    """The Job is saved inside its last step, a moment before DBOS marks the workflow finished."""
    deadline = time.monotonic() + timeout
    while (st := await asyncio.to_thread(workflow_status, org_id, job_id)) in ("PENDING", "ENQUEUED") \
            and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return st


def step_names(org_id, job_id) -> list[str]:
    return [s["function_name"] for s in DBOS.list_workflow_steps(orchestrator.workflow_id(org_id, job_id))]


def restart_dbos() -> None:
    """Stop and relaunch DBOS from a plain thread. DBOS made its executor this event loop's default, and shutting
    DBOS down closes that executor, so the loop gets a fresh one (a real restart is a new process and loop)."""
    t = threading.Thread(target=lambda: (durable.shutdown(), durable.launch()))
    t.start()
    t.join()
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor())


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so neither the stage nor DBOS records it as a result."""


# ---------------------------------------------------------------- one step per stage

async def test_each_stage_is_one_checkpointed_step(org, no_network):
    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "steps"))
    await orchestrator.run_job(org, job.id)
    assert store.get_job(org, job.id).status == JobStatus.READY_FOR_REVIEW
    names = await asyncio.to_thread(step_names, org, job.id)
    assert names == ["research", "verify", "photos", "copy", "stock", "render", "qa", "review", "safeguard"]
    assert await workflow_done(org, job.id) == "SUCCESS"
    # Checkpoints are plain JSON and small: the Job document holds the large outputs (deck, briefs, log).
    with store.system() as c:
        rows = c.execute("select serialization, length(output) from dbos.operation_outputs where workflow_uuid = %s",
                         (orchestrator.workflow_id(org, job.id),)).fetchall()
    assert {s for s, _ in rows} == {"portable_json"}
    assert max(n for _, n in rows) < 20_000


async def test_brainstorm_job_skips_research(org, no_network):
    from app import brainstorms
    board = store.save_brainstorm(org, brainstorms.sample_brainstorm())
    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "bs", "brainstorm", board.id))
    await orchestrator.run_job(org, job.id, brainstorm=board)
    assert store.get_job(org, job.id).status == JobStatus.READY_FOR_REVIEW
    assert (await asyncio.to_thread(step_names, org, job.id))[0] == "verify"


# ---------------------------------------------------------------- crash, restart, resume

async def test_restart_resumes_at_the_interrupted_stage_without_redoing_finished_ones(org, no_network,
                                                                                     monkeypatch):
    calls: Counter[str] = Counter()
    real = dict(orchestrator.STAGE_FUNCS)

    def counted(name):
        async def run(s):
            calls[name] += 1
            if name == "render" and calls[name] == 1:  # the process dies half-way through Render
                s.r.step("render", "running")
                await s.r.flush()
                raise Crash()
            await real[name](s)
        return run
    for name in real:
        monkeypatch.setitem(orchestrator.STAGE_FUNCS, name, counted(name))

    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "crash test"))
    with pytest.raises(Crash):
        await orchestrator.run_job(org, job.id)
    assert store.get_job(org, job.id).status == JobStatus.RENDERING
    assert await asyncio.to_thread(workflow_status, org, job.id) == "PENDING"

    # Startup reconciliation leaves a Job whose workflow will resume alone (only dead ones are failed).
    from app.main import recover_interrupted
    await asyncio.to_thread(recover_interrupted)
    assert store.get_job(org, job.id).status == JobStatus.RENDERING

    # "Restart": stop DBOS and launch it again, as the API's lifespan does. Launch resumes PENDING workflows.
    restart_dbos()
    done = await wait_for(org, job.id)

    assert done.status == JobStatus.READY_FOR_REVIEW, done.error
    assert calls == {"research": 1, "verify": 1, "photos": 1, "copy": 1, "stock": 1,
                     "render": 2, "qa": 1, "review": 1, "safeguard": 1}
    assert any("cut off by a server restart" in line.message for line in done.log)
    assert len(done.slide_urls) == len(done.deck.slides)
    assert await workflow_done(org, job.id) == "SUCCESS"


async def test_a_job_that_cannot_resume_is_failed_at_startup(org):
    from app.main import recover_interrupted
    stuck = orchestrator.new_job(secrets.token_hex(3), "never queued")
    stuck.status = JobStatus.WRITING  # e.g. started before Durable Jobs, or its workflow is gone
    store.save_job(org, stuck)
    await asyncio.to_thread(recover_interrupted)
    after = store.get_job(org, stuck.id)
    assert after.status == JobStatus.FAILED and "could not be resumed" in after.error


# ---------------------------------------------------------------- per-Organization cap

@pytest.fixture
def one_per_org():
    durable.register_queue(per_org=1, poll_s=0.1)
    # The queue worker reads its limits, then sleeps one poll interval: let it pick up these before enqueueing.
    time.sleep(durable.QUEUE_POLL_S + 0.5)
    yield
    durable.register_queue()


async def test_each_organization_runs_one_job_at_a_time_without_blocking_the_other(one_per_org, monkeypatch):
    a = orgs.create_org(uuid.uuid4(), "Busy org").org_id
    b = orgs.create_org(uuid.uuid4(), "Other org").org_id
    spans: dict[str, list[float]] = {}
    seen: list[tuple[uuid.UUID, str]] = []

    def stage(name):
        async def run(s):
            seen.append((s.r.org_id, s.job.id))
            span = spans.setdefault(s.job.id, [time.monotonic(), 0.0])
            await asyncio.sleep(0.15)
            span[1] = time.monotonic()
            if name == "review":
                s.job.status = JobStatus.READY_FOR_REVIEW
        return run
    for name in orchestrator.STAGE_FUNCS:
        monkeypatch.setitem(orchestrator.STAGE_FUNCS, name, stage(name))

    jobs_a = [store.save_job(a, orchestrator.new_job(secrets.token_hex(3), f"A{i}")) for i in range(3)]
    job_b = store.save_job(b, orchestrator.new_job(secrets.token_hex(3), "B"))
    for j in jobs_a:
        await orchestrator.start_job(a, j)
    await orchestrator.start_job(b, job_b)
    for j in jobs_a:
        await wait_for(a, j.id, timeout=90)
    await wait_for(b, job_b.id, timeout=90)

    def overlap(x, y):
        return spans[x][0] < spans[y][1] and spans[y][0] < spans[x][1]
    ids_a = [j.id for j in jobs_a]
    assert not any(overlap(x, y) for x in ids_a for y in ids_a if x < y), spans  # A: one at a time
    assert any(overlap(x, job_b.id) for x in ids_a), spans  # B ran while A's backlog was still going
    # Each workflow only touched its own Organization's Job, and B can't see A's.
    mine = {a: set(ids_a), b: {job_b.id}}
    assert all(job_id in mine[o] for o, job_id in seen)
    assert all(store.get_job(b, j) is None for j in ids_a) and store.get_job(a, job_b.id) is None
    st = await asyncio.to_thread(DBOS.get_workflow_status, orchestrator.workflow_id(a, ids_a[0]))
    assert (st.queue_name, st.queue_partition_key) == (durable.JOB_QUEUE, str(a))


async def test_queueing_the_same_job_twice_runs_it_once(org, monkeypatch):
    calls: Counter[str] = Counter()

    async def research(s):
        calls[s.job.id] += 1
        raise RuntimeError("stop here")
    monkeypatch.setitem(orchestrator.STAGE_FUNCS, "research", research)
    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "twice"))
    await orchestrator.start_job(org, job)
    await orchestrator.start_job(org, job)
    await wait_for(org, job.id)
    await asyncio.sleep(1.5)  # a second workflow would have been dequeued by now
    assert calls == {job.id: 1}


# ---------------------------------------------------------------- fixture mode through the API

async def test_fixture_quick_job_runs_end_to_end_through_the_queue(client, no_network):
    r = await asyncio.to_thread(client.post, "/jobs", json={"prompt": "Seongsu pop-ups", "scenario": "good"})
    assert r.status_code == 201 and r.json()["status"] == "QUEUED"
    job = await wait_for(client.org_id, r.json()["id"])
    assert job.status == JobStatus.READY_FOR_REVIEW and job.slide_urls
    assert await workflow_done(client.org_id, job.id) == "SUCCESS"


async def test_fixture_brainstorm_job_runs_end_to_end_through_the_queue(client, no_network):
    board = (await asyncio.to_thread(client.post, "/brainstorms", json={"sample": True})).json()
    r = await asyncio.to_thread(client.post, f"/brainstorms/{board['id']}/generate")
    assert r.status_code == 201
    job = await wait_for(client.org_id, r.json()["id"])
    assert job.status == JobStatus.READY_FOR_REVIEW and job.source == "brainstorm"
    assert job.briefs[0].title_en == "Mangwon Night Market"


# ---------------------------------------------------------------- where DBOS keeps its tables

def test_dbos_schema_is_closed_to_the_api_role(org):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.tenant(org) as c:
        c.execute("select count(*) from dbos.workflow_status")
    with store.system() as c:
        usage = c.execute("select has_schema_privilege('wok_api', 'dbos', 'usage'), "
                          "has_schema_privilege('public', 'dbos', 'usage')").fetchone()
        tables = c.execute("select count(*) from pg_tables where schemaname = 'dbos'").fetchone()[0]
        public_dbos = c.execute("select count(*) from pg_tables where schemaname = 'public' "
                                "and tablename like '%workflow%'").fetchone()[0]
    assert usage == (False, False)
    assert tables > 0 and public_dbos == 0  # DBOS's tables live in `dbos`, none leaked into `public`
