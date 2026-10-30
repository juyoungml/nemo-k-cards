"""Operator command: attach an Instagram account we already hold a token for (our own @whatsonkorea, from the
hackathon's IG_USER_ID / IG_ACCESS_TOKEN) to a Pilot Organization, without the OAuth flow customers use (#15).

    uv run python -m app.instagram_import <org-id>

Reads IG_ACCESS_TOKEN (and IG_USER_ID, if set, as a cross-check) from the environment or backend/.env, asks Instagram
who the token belongs to, refuses personal accounts, then stores it exactly like a connect does: token in Vault,
row in `instagram_accounts`, `instagram.connected` in the audit log under the Organization's first Admin. The same
Pilot, account-limit and one-Organization-per-account checks apply. The token is never printed."""

import os
import sys
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values

from app import instagram_accounts, instagram_api, store

USAGE = __doc__.split("\n\n")[1]
REFUSED = {
    "not_pilot": "this Organization isn't a Pilot: `uv run python -m app.pilot on <org-id>` first",
    "not_admin": "this Organization has no Admin to record the connection under",
    "limit": "this Organization is at its Instagram account limit (`app.pilot limit <org-id> <n>`)",
    "taken": "this Instagram account is already connected to another Organization",
}


def _credentials() -> tuple[str, str]:
    env = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    return (env.get("IG_ACCESS_TOKEN") or "").strip(), (env.get("IG_USER_ID") or "").strip()


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        sys.exit(USAGE)
    try:
        org_id = UUID(argv[0])
    except ValueError:
        sys.exit(f"not an Organization id: {argv[0]!r} (see `uv run python -m app.pilot list`)")
    token, expected_user = _credentials()
    if not token:
        sys.exit("IG_ACCESS_TOKEN isn't set (environment or backend/.env)")
    with store.system() as c:
        row = c.execute("select user_id from members where org_id = %s and role = 'admin' order by created_at "
                        "limit 1", (org_id,)).fetchone()
    if row is None:
        sys.exit(REFUSED["not_admin"])
    try:
        profile = instagram_api.me(instagram_api.Token(token))
    except instagram_api.InstagramError as e:
        sys.exit(f"Instagram refused the token: {e}")
    if expected_user and profile.user_id != expected_user:
        sys.exit(f"the token belongs to @{profile.username}, not IG_USER_ID {expected_user}")
    if not profile.professional:
        sys.exit(f"@{profile.username} is a personal account; only Business or Creator accounts can post")
    try:
        account_id = instagram_accounts.save(org_id, row[0], profile, instagram_api.Token(token))
    except instagram_accounts.Refused as e:
        sys.exit(REFUSED.get(e.code, e.code))
    print(f"@{profile.username} ({profile.account_type.lower()}) connected to Organization {org_id} "
          f"as account {account_id}; expiry unknown until the next token refresh (#22)")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    finally:
        store.close()
