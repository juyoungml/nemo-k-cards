"""The deploy guard (`python -m app.migration_check`): lists migration files the database hasn't applied."""

import os

import psycopg
import pytest

from app import migration_check


@pytest.fixture
def db():
    # Its own transaction, rolled back: the migration history table never outlives the test.
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        yield c
        c.rollback()


def files(tmp_path, *versions):
    for v in versions:
        (tmp_path / f"{v}_something.sql").write_text("select 1;")
    return tmp_path


def history(c, *versions):
    c.execute("create schema if not exists supabase_migrations")
    c.execute("create table supabase_migrations.schema_migrations (version text primary key)")
    for v in versions:
        c.execute("insert into supabase_migrations.schema_migrations values (%s)", (v,))


def test_all_applied(db, tmp_path):
    history(db, "20261029000000", "20261030110000")
    assert migration_check.missing(db, files(tmp_path, "20261029000000", "20261030110000")) == []


def test_lists_what_is_missing_in_order(db, tmp_path):
    history(db, "20261029000000")
    d = files(tmp_path, "20261030110000", "20261029000000", "20261030100000")
    assert migration_check.missing(db, d) == ["20261030100000", "20261030110000"]


def test_database_ahead_of_the_code_is_fine(db, tmp_path):
    # A rollback to older code against a newer schema: nothing in the code is missing.
    history(db, "20261029000000", "20261031000000")
    assert migration_check.missing(db, files(tmp_path, "20261029000000")) == []


def test_no_history_means_unknown(db, tmp_path):
    assert migration_check.applied(db) is None
    assert migration_check.missing(db, files(tmp_path, "20261029000000")) is None


def test_ships_every_repo_migration():
    assert migration_check.shipped()[0] == "20261029000000"
    assert len(migration_check.shipped()) == len(list(migration_check.MIGRATIONS.glob("*.sql")))
