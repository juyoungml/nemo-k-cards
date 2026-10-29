"""Organization isolation (#13): Organization B can never list, read, edit, approve, reject or post Organization
A's Jobs and Brainstorms, or see A's policy events. Proved twice: through the API, and directly in Postgres as
the API role (`wok_api`) with `app.org_id` set, where raw SQL tries to get around the policies."""

import json
import re
import secrets
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app import brainstorms, orgs, store
from app.config import settings
from app.pipeline import agent_runner, orchestrator
from app.schemas import Brainstorm, BrainstormUpdate, Job, JobStatus, PolicyEvent
from tests.factories import allowed_job

MIGRATIONS = sorted((Path(__file__).resolve().parents[2] / "supabase/migrations").glob("*.sql"))
ISOLATION = next(m for m in MIGRATIONS if "org_isolation" in m.name)
ORG_TABLES = ("jobs", "brainstorms", "policy_events")


def _ready_job(org_id, prompt="A's secret plan") -> Job:
    return store.save_job(org_id, allowed_job(prompt))


def _event(job_id: str, host: str) -> PolicyEvent:
    return PolicyEvent(time="12:00:00", sandbox="sb", binary="curl", host=host, request="GET /",
                       result="policy_denied", job_id=job_id)


@pytest.fixture
def two(sign_in):
    """Organizations A and B, each with a signed-in Admin, and some of A's work."""
    a, b = sign_in(org="A"), sign_in(org="B")
    job = _ready_job(a.org_id)
    board = a.post("/brainstorms", json={"sample": True}).json()
    store.add_policy_events(a.org_id, [_event(job.id, "a-only.example")])
    return a, b, job, board


# ---------------------------------------------------------------- API

def test_b_cannot_list_or_read_a_jobs(two):
    a, b, job, _ = two
    assert job.id in [j["id"] for j in a.get("/jobs").json()]
    assert job.id not in [j["id"] for j in b.get("/jobs?limit=500").json()]
    assert a.get(f"/jobs/{job.id}").status_code == 200
    assert b.get(f"/jobs/{job.id}").status_code == 404
    assert b.get(f"/jobs/{job.id}/events").status_code == 404


def test_b_cannot_approve_reject_or_post_a_job(two, monkeypatch):
    a, b, job, _ = two
    published = []

    async def fake_publish(org_id, job_id, caption, mode):
        published.append((org_id, job_id))
    monkeypatch.setattr(orchestrator, "publish_job", fake_publish)
    assert b.post(f"/jobs/{job.id}/approve", json={}).status_code == 404
    assert b.post(f"/jobs/{job.id}/reject", json={"reason": "B says no"}).status_code == 404
    assert published == []
    assert store.get_job(a.org_id, job.id).status == JobStatus.READY_FOR_REVIEW  # untouched
    assert a.post(f"/jobs/{job.id}/approve", json={}).status_code == 200  # control: A can
    assert published == [(a.org_id, job.id)]


async def test_publishing_with_the_wrong_organization_does_nothing(two):
    a, b, job, _ = two
    await orchestrator.publish_job(b.org_id, job.id, None, "mock")
    await orchestrator.run_job(b.org_id, job.id)
    after = store.get_job(a.org_id, job.id)
    assert after.status == JobStatus.READY_FOR_REVIEW and after.publish_progress is None and after.log == []


def test_b_cannot_read_edit_message_or_generate_from_a_brainstorm(two):
    a, b, _, board = two
    bid = board["id"]
    assert b.get(f"/brainstorms/{bid}").status_code == 404
    assert b.patch(f"/brainstorms/{bid}", json={"tones": ["hijacked"]}).status_code == 404
    assert b.post(f"/brainstorms/{bid}/messages", json={"text": "다른 앵글"}).status_code == 404
    assert b.post(f"/brainstorms/{bid}/generate").status_code == 404
    mine = a.get(f"/brainstorms/{bid}").json()
    assert mine == board and mine["job_id"] is None


def test_b_does_not_see_a_policy_events_or_counts(two):
    a, b, _, _ = two
    hosts = lambda c: {e["host"] for e in c.get("/policy-events").json()["events"]}
    assert "a-only.example" in hosts(a)
    assert "a-only.example" not in hosts(b)
    before = b.get("/metrics").json()
    _ready_job(a.org_id)
    assert b.get("/metrics").json() == before


def test_new_work_belongs_to_the_caller(sign_in, monkeypatch):
    async def no_run(*args, **kwargs):
        pass
    monkeypatch.setattr(orchestrator, "start_job", no_run)
    a, b = sign_in(org="A"), sign_in(org="B")
    jid = a.post("/jobs", json={"prompt": "Seongsu pop-ups"}).json()["id"]
    bid = b.post("/brainstorms", json={"sample": False}).json()["id"]
    with store.system() as c:
        owners = dict(c.execute("select id, org_id from jobs where id = %s union all "
                                "select id, org_id from brainstorms where id = %s", (jid, bid)).fetchall())
    assert owners == {jid: a.org_id, bid: b.org_id}


def test_new_ids_are_long_enough_to_be_unique_across_organizations(sign_in, monkeypatch):
    async def no_run(*args, **kwargs):
        pass
    monkeypatch.setattr(orchestrator, "start_job", no_run)
    a = sign_in(org="A")
    jid = a.post("/jobs", json={"prompt": "Seongsu pop-ups"}).json()["id"]
    bid = a.post("/brainstorms", json={"sample": False}).json()["id"]
    assert re.fullmatch(r"[0-9a-f]{16}", jid) and re.fullmatch(r"d-[0-9a-f]{16}", bid)


def test_same_id_in_another_organization_cannot_overwrite(two):
    a, b, job, _ = two
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.save_job(b.org_id, job.model_copy(update={"prompt": "overwritten by B"}))
    assert store.get_job(a.org_id, job.id).prompt == "A's secret plan"


# ---------------------------------------------------------------- what the agent sandbox receives

class FakeRunner:
    """Stands in for the sandbox: records what each stage was given."""

    def __init__(self):
        self.calls: list[tuple[str, str, uuid.UUID]] = []

    async def run(self, agent, task, payload, schema, files, job_id, org_id):
        self.calls.append((agent, json.dumps(payload, ensure_ascii=False, default=str), org_id))
        return BrainstormUpdate(reply="ok").model_dump()


def test_sandbox_gets_only_the_callers_brainstorm(sign_in, monkeypatch):
    a, b = sign_in(org="A"), sign_in(org="B")
    store.save_brainstorm(a.org_id, Brainstorm(id="d-a-secret", tones=["A-ONLY-MARKER"]))
    mine = b.post("/brainstorms", json={"sample": False}).json()["id"]
    runner = FakeRunner()
    monkeypatch.setattr(agent_runner, "get_runner", lambda: runner)
    monkeypatch.setattr(settings, "demo_mode", "live")
    assert b.post("/brainstorms/d-a-secret/messages", json={"text": "hi"}).status_code == 404
    assert runner.calls == []  # never reached a sandbox
    assert b.post(f"/brainstorms/{mine}/messages", json={"text": "hi"}).status_code == 200
    [(agent, payload, org_id)] = runner.calls
    assert agent == "planner" and org_id == b.org_id
    assert "A-ONLY-MARKER" not in payload and "d-a-secret" not in payload


async def test_sandbox_policy_events_are_stored_under_the_jobs_organization(sign_in, monkeypatch):
    """OpenShell path with the CLI faked: the sandbox is labelled with the Organization and its policy log
    lands in that Organization only."""
    a, b = sign_in(org="A"), sign_in(org="B")
    log = (Path(__file__).parent / "data/openshell-probe.txt").read_text()
    seen: list[tuple[str, ...]] = []

    async def fake_cli(self, *args, stdin=None, timeout=120):
        seen.append(args)
        if args[:2] == ("sandbox", "exec"):
            return 0, json.dumps({"structured_output": {"reply": "ok"}}).encode(), b""
        if args[0] == "logs":
            return 0, log.encode(), b""
        return 0, b"", b""
    monkeypatch.setattr(agent_runner.OpenShellRunner, "_cli", fake_cli)
    monkeypatch.setattr(agent_runner.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(settings, "agent_runner", "openshell")
    await agent_runner.run_stage("planner", "t", {}, BrainstormUpdate, job_id="d-x1", org_id=a.org_id)
    create = next(s for s in seen if s[:2] == ("sandbox", "create"))
    assert f"org={a.org_id}" in create
    assert {e.job_id for e in store.list_policy_events(a.org_id)} == {"d-x1"}
    assert store.list_policy_events(b.org_id) == []


# ---------------------------------------------------------------- database, as the API role

def _as_api(c: psycopg.Connection, org_id=None, user_id=None) -> None:
    c.execute("set local role wok_api")
    if org_id is not None:
        c.execute("select set_config('app.org_id', %s, true)", (str(org_id),))
    if user_id is not None:
        c.execute("select set_config('app.user_id', %s, true)", (str(user_id),))


def test_db_wrong_organization_sees_and_changes_nothing(two):
    a, b, job, board = two
    with store.tenant(b.org_id) as c:
        assert c.execute("select count(*) from jobs where id = %s or org_id = %s", (job.id, a.org_id)).fetchone()[0] == 0
        assert c.execute("select count(*) from brainstorms where id = %s", (board["id"],)).fetchone()[0] == 0
        assert c.execute("select count(*) from policy_events where org_id = %s", (a.org_id,)).fetchone()[0] == 0
        assert c.execute("update jobs set doc = '{}' where id = %s", (job.id,)).rowcount == 0
        assert c.execute("update jobs set org_id = %s where id = %s", (b.org_id, job.id)).rowcount == 0
        assert c.execute("update brainstorms set doc = '{}' where id = %s", (board["id"],)).rowcount == 0
    for sql, params in [
        ("insert into jobs (id, org_id, created_at, doc) values ('x-' || md5(random()::text), %s, now(), '{}')",
         (a.org_id,)),
        ("insert into brainstorms (id, org_id, doc) values ('x-' || md5(random()::text), %s, '{}')", (a.org_id,)),
        ("insert into policy_events (job_id, org_id, doc) values ('x', %s, '{}')", (a.org_id,)),
        ("delete from jobs where id = %s", (job.id,)),  # no delete grant at all
        ("delete from policy_events", ()),
        ("update policy_events set doc = '{}'", ()),  # append-only
        ("update orgs set name = 'pwned' where id = %s", (a.org_id,)),
        ("insert into members (org_id, user_id, role) values (%s, %s, 'admin')", (a.org_id, b.user_id)),
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), store.tenant(b.org_id) as c:
            c.execute(sql, params)
    assert store.get_job(a.org_id, job.id).prompt == "A's secret plan"


def test_db_own_organization_moving_a_row_out_is_refused(two):
    a, b, job, _ = two
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.tenant(a.org_id) as c:
        c.execute("update jobs set org_id = %s where id = %s", (b.org_id, job.id))


@pytest.mark.parametrize("setting", [None, ""])
def test_db_without_an_organization_sees_and_writes_nothing(two, setting):
    with store._db().connection() as c, c.transaction(force_rollback=True):
        _as_api(c)
        if setting is not None:
            c.execute("select set_config('app.org_id', %s, true)", (setting,))
        for t in (*ORG_TABLES, "orgs", "members"):
            assert c.execute(f"select count(*) from {t}").fetchone()[0] == 0, t
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)), c.transaction():
            c.execute("insert into brainstorms (id, doc) values ('orphan', '{}')")  # no org: the row can't exist
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("insert into brainstorms (id, org_id, doc) values ('orphan', %s, '{}')", (two[0].org_id,))


def test_db_malformed_organization_fails_closed(two):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation), \
            store._db().connection() as c, c.transaction(force_rollback=True):
        _as_api(c, org_id="' or true --")
        c.execute("select count(*) from jobs").fetchone()


def test_db_membership_lookup_sees_only_the_callers_rows(two):
    _, b, *_ = two
    with store._db().connection() as c, c.transaction(force_rollback=True):
        _as_api(c, user_id=b.user_id)
        assert c.execute("select org_id from members").fetchall() == [(b.org_id,)]
        assert c.execute("select id from orgs").fetchall() == [(b.org_id,)]
        assert c.execute("select count(*) from jobs").fetchone()[0] == 0  # a user scope is not an org scope


@pytest.mark.parametrize("reset", [None, store._reset], ids=["no-reset-callback", "reset-callback"])
def test_db_settings_do_not_leak_across_pooled_connections(two, monkeypatch, reset):
    """One physical connection: a tenant transaction for A (committed, then one that fails) leaves nothing
    behind for the next borrower. Holds from the transaction-local settings alone, without the pool's reset."""
    a, *_ = two
    pool = ConnectionPool(settings.database_url, min_size=1, max_size=1, open=True, reset=reset,
                          kwargs={"prepare_threshold": None})
    monkeypatch.setattr(store, "_pool", pool)
    try:
        def after() -> tuple:
            with pool.connection() as c:
                return c.execute("select current_user, coalesce(current_setting('app.org_id', true), ''), "
                                 "(select count(*) from jobs)").fetchone()

        owner, _, total = after()
        with store.tenant(a.org_id) as c:
            pid = c.info.backend_pid
            assert c.execute("select current_user").fetchone()[0] == "wok_api"
        assert after() == (owner, "", total) and owner != "wok_api"
        with pytest.raises(psycopg.errors.DivisionByZero), store.tenant(a.org_id) as c:
            c.execute("select 1/0 from jobs")  # a database error: rolled back
        with pytest.raises(RuntimeError), store.tenant(a.org_id):
            raise RuntimeError("handler crashed mid-transaction")
        assert after() == (owner, "", total)
        with pool.connection() as c:
            assert c.info.backend_pid == pid  # really the same connection
    finally:
        pool.close()


def test_db_concurrent_requests_stay_in_their_organization(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    mine = {a.org_id: {_ready_job(a.org_id).id for _ in range(3)}, b.org_id: {_ready_job(b.org_id).id for _ in range(3)}}
    wrong = []

    def worker(org_id):
        for _ in range(40):
            got = {j.id for j in store.list_jobs(org_id, 500)}
            if got != mine[org_id]:
                wrong.append((org_id, got))
    threads = [threading.Thread(target=worker, args=(o,)) for o in (a.org_id, b.org_id) * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wrong == []


def test_restart_recovery_keeps_each_job_in_its_organization(sign_in):
    from app.main import recover_interrupted

    a, b = sign_in(org="A"), sign_in(org="B")
    ja = store.save_job(a.org_id, orchestrator.new_job(secrets.token_hex(3), "A running"))  # QUEUED
    jb = _ready_job(b.org_id).model_copy(update={"status": JobStatus.PUBLISHING})
    store.save_job(b.org_id, jb)
    recover_interrupted()
    assert store.get_job(a.org_id, ja.id).status == JobStatus.FAILED
    assert store.get_job(b.org_id, jb.id).status == JobStatus.READY_FOR_REVIEW
    assert store.get_job(a.org_id, jb.id) is None and store.get_job(b.org_id, ja.id) is None


def test_tenant_needs_an_organization():
    with pytest.raises(ValueError), store.tenant(None):
        pass


# ---------------------------------------------------------------- schema guarantees

def test_every_public_table_has_rls_and_policies_only_for_the_api_role():
    with store.system() as c:
        no_rls = c.execute("select relname from pg_class where relnamespace = 'public'::regnamespace "
                           "and relkind in ('r', 'p') and not relrowsecurity").fetchall()
        no_policy = c.execute("select relname from pg_class t where relnamespace = 'public'::regnamespace "
                              "and relkind = 'r' and not exists (select 1 from pg_policies p "
                              "where p.schemaname = 'public' and p.tablename = t.relname)").fetchall()
        roles = {r for (rs,) in c.execute("select roles from pg_policies where schemaname = 'public'") for r in rs}
        api = c.execute("select rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname = 'wok_api'").fetchone()
        owns = c.execute("select count(*) from pg_class where relowner = 'wok_api'::regrole").fetchone()[0]
    assert no_rls == [] and no_policy == []
    assert roles == {"wok_api"}  # nothing for anon, authenticated or public
    assert api == (False, False, False) and owns == 0


def test_org_column_is_on_every_organization_owned_table():
    with store.system() as c:
        rows = c.execute("select table_name from information_schema.columns where table_schema = 'public' "
                         "and column_name = 'org_id'").fetchall()
    assert {r[0] for r in rows} >= {*ORG_TABLES, "members"}


def _migrate_scratch(c: psycopg.Connection, before) -> None:
    """Apply the migrations into a scratch schema (the caller rolls everything back); `before(c, schema)` runs
    just before the isolation migration."""
    schema = f"mig_{secrets.token_hex(4)}"
    c.execute(f"create schema {schema}")
    c.execute(f"set local search_path to {schema}")
    for m in MIGRATIONS:
        if m.name < ISOLATION.name:
            c.execute(m.read_text())
    before(c, schema)
    c.execute(ISOLATION.read_text())


def test_supabase_api_roles_get_nothing():
    """On Supabase, anon and authenticated (PostgREST) start with every privilege on tables in the exposed
    schema. Simulated here, roles included (all rolled back): after the migration they can't touch a table."""
    def supabase_defaults(c, schema):
        for r in ("anon", "authenticated"):
            if not c.execute("select 1 from pg_roles where rolname = %s", (r,)).fetchone():
                c.execute(f"create role {r} nologin")
            c.execute(f"grant usage on schema {schema} to {r}")
            c.execute(f"grant all on all tables in schema {schema} to {r}")
    with store.system() as c, c.transaction(force_rollback=True):
        _migrate_scratch(c, supabase_defaults)
        for role in ("anon", "authenticated"):
            for t in (*ORG_TABLES, "orgs", "members"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                    c.execute(f"set local role {role}")
                    c.execute(f"select * from {t}")


def test_rows_from_before_organizations_stay_hidden_until_assigned():
    """Legacy rows (no org_id) survive the migration, are invisible to every Organization and can't be changed
    until an operator assigns them; then the NOT VALID check can be validated."""
    legacy = Job(id="legacy1", prompt="old", created_at="2026-10-01T00:00:00Z")

    def old_rows(c, schema):
        c.execute(f"grant usage on schema {schema} to wok_api")
        c.execute("insert into jobs (id, created_at, doc) values (%s, %s, %s)",
                  (legacy.id, legacy.created_at, legacy.model_dump_json()))
    with store.system() as c, c.transaction(force_rollback=True):
        _migrate_scratch(c, old_rows)
        org = c.execute("insert into orgs (name, created_by) values ('Owner', gen_random_uuid()) returning id").fetchone()[0]
        assert c.execute("select org_id from jobs where id = 'legacy1'").fetchone() == (None,)
        with c.transaction(force_rollback=True):
            _as_api(c, org_id=org)
            assert c.execute("select count(*) from jobs").fetchone()[0] == 0
            assert c.execute("update jobs set doc = doc where id = 'legacy1'").rowcount == 0
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():  # touched without an org: refused
            c.execute("update jobs set doc = doc where id = 'legacy1'")
        c.execute("update jobs set org_id = %s where org_id is null", (org,))
        c.execute("alter table jobs validate constraint jobs_org_id_required")
        _as_api(c, org_id=org)
        assert c.execute("select id from jobs").fetchall() == [("legacy1",)]


def test_brainstorm_helpers_scope_to_the_organization(org):
    d = brainstorms.create(org, sample=False)
    other = orgs.create_org(uuid.uuid4(), "Other").org_id
    assert store.get_brainstorm(org, d.id) == d and store.get_brainstorm(other, d.id) is None
