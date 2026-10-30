"""Deploy guard: does the database have every migration this code ships with?

    python -m app.migration_check

`wok` runs it from the new API image before it restarts anything (deploy/README.md). The Supabase CLI records each
applied migration's version (the timestamp before the first underscore) in `supabase_migrations.schema_migrations`;
a migration file whose version isn't there hasn't been applied (`supabase db push`), and code that reads its columns
would fail. Exit codes: 0 all applied, 1 some missing, 2 can't tell (no connection, or no migration history).
Prints versions only, never the connection string."""

import sys
from pathlib import Path

import psycopg

from app.config import settings

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase/migrations"


def shipped(directory: Path = MIGRATIONS) -> list[str]:
    return sorted({p.name.split("_", 1)[0] for p in directory.glob("*.sql")})


def applied(c: psycopg.Connection) -> set[str] | None:
    """Versions the database has applied, or None when it keeps no Supabase migration history."""
    if c.execute("select to_regclass('supabase_migrations.schema_migrations')").fetchone()[0] is None:
        return None
    return {row[0] for row in c.execute("select version from supabase_migrations.schema_migrations")}


def missing(c: psycopg.Connection, directory: Path = MIGRATIONS) -> list[str] | None:
    done = applied(c)
    return None if done is None else [v for v in shipped(directory) if v not in done]


def main() -> int:
    versions = shipped()
    if not versions:
        print(f"migration check: no migrations found in {MIGRATIONS}", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(settings.database_url, connect_timeout=10, application_name="wok_migration_check") as c:
            gap = missing(c)
    except psycopg.Error as e:
        print(f"migration check: can't reach the database ({type(e).__name__})", file=sys.stderr)
        return 2
    if gap is None:
        print("migration check: the database has no Supabase migration history; can't tell", file=sys.stderr)
        return 2
    if gap:
        print(f"migration check: the database lacks {len(gap)} of {len(versions)} migrations: {' '.join(gap)}. "
              "Apply them first (`supabase db push`).", file=sys.stderr)
        return 1
    print(f"migration check: all {len(versions)} migrations applied (latest {versions[-1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
