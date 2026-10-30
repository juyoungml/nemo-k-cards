"""Supabase CLI tracks migrations by the timestamp before the first underscore: two files with the same one
can't both be recorded, and `supabase db push` / `migration up` stops at the second."""

from collections import Counter
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase/migrations"


def test_every_migration_has_its_own_version():
    versions = Counter(p.name.split("_", 1)[0] for p in MIGRATIONS.glob("*.sql"))
    assert versions, "no migrations found"
    assert [v for v, n in versions.items() if n > 1] == []
