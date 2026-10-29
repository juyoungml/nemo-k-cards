"""Operator command for Pilot Organizations (GLOSSARY) and their Instagram account limit. The API can't change either:
`wok_api` only reads `orgs`. This runs as DATABASE_URL's role, so run it on the host that holds that secret.

    uv run python -m app.pilot list
    uv run python -m app.pilot on  <org-id>      # after adding its Instagram account as a tester on our Meta app
    uv run python -m app.pilot off <org-id>
    uv run python -m app.pilot limit <org-id> <n|default>

The same in SQL (Supabase SQL editor): `update orgs set is_pilot = true where id = '<org-id>';`"""

import sys
from uuid import UUID

from app import store
from app.config import settings

USAGE = __doc__.split("\n\n")[1]


def _org(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        sys.exit(f"not an Organization id: {raw!r} (see `list`)")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(USAGE)
    cmd, args = argv[0], argv[1:]
    with store.system() as c:
        if cmd == "list" and not args:
            rows = c.execute("select o.id, o.name, o.is_pilot, o.instagram_account_limit, "
                             "(select count(*) from instagram_accounts a where a.org_id = o.id "
                             "and a.status <> 'disconnected') from orgs o order by o.created_at").fetchall()
            for org_id, name, pilot, limit, used in rows:
                shown = f"{limit}" if limit is not None else f"default {settings.instagram_accounts_per_org}"
                print(f"{org_id}  {'PILOT' if pilot else '     '}  accounts {used}/{shown}  {name}")
            return
        if cmd in ("on", "off") and len(args) == 1:
            row = c.execute("update orgs set is_pilot = %s where id = %s returning name",
                            (cmd == "on", _org(args[0]))).fetchone()
        elif cmd == "limit" and len(args) == 2:
            n = None if args[1] == "default" else int(args[1])
            if n is not None and n < 0:
                sys.exit("the limit can't be negative")
            row = c.execute("update orgs set instagram_account_limit = %s where id = %s returning name",
                            (n, _org(args[0]))).fetchone()
        else:
            sys.exit(USAGE)
        if row is None:
            sys.exit("no such Organization")
        print(f"{row[0]}: {' '.join(argv)}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    finally:
        store.close()
