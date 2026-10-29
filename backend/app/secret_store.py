"""Where Organization secrets live (docs/spec/v1.md pseudocode 2 `SecretStore`, decision D3, ADR 0001): today only
Instagram access tokens, in Supabase Vault.

Vault encrypts each secret at rest and decrypts it only through the `vault.decrypted_secrets` view. Only the
connecting role (the privileged backend path, `store.system()`) can use it: the API's RLS role `wok_api`, Supabase's
`anon` and `authenticated`, and the agent sandbox (which has no database access at all) can't.

The methods take an open transaction, so a token and the row that points at it are saved together or not at all.
A store that isn't Postgres-backed (OpenBao, a cloud secret manager) would ignore it.

There is deliberately no weaker fallback in this module: a server whose database has no Vault refuses to connect
Instagram accounts (`SecretStoreUnavailable`) rather than keep tokens somewhere else. The tests' plain Postgres gets
a stand-in `vault` schema from backend/tests/vault_shim.sql, so CI runs this same code."""

from typing import Protocol
from uuid import UUID

from psycopg import Connection


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretStore(Protocol):
    def available(self, c: Connection) -> bool: ...
    def put(self, c: Connection, name: str, value: str, description: str = "") -> UUID: ...  # create or replace
    def get(self, c: Connection, ref: UUID) -> str | None: ...
    def delete(self, c: Connection, ref: UUID) -> None: ...


class VaultSecretStore:
    """Supabase Vault. `c` must be a privileged transaction (`store.system()`): `wok_api` has no access to `vault`."""

    def available(self, c: Connection) -> bool:
        row = c.execute("select to_regprocedure('vault.create_secret(text,text,text,uuid)') is not null "
                        "and to_regprocedure('vault.update_secret(uuid,text,text,text,uuid)') is not null "
                        "and to_regclass('vault.decrypted_secrets') is not null").fetchone()
        return bool(row and row[0])

    def _check(self, c: Connection) -> None:
        if not self.available(c):
            raise SecretStoreUnavailable("Supabase Vault is not installed in this database")

    def put(self, c: Connection, name: str, value: str, description: str = "") -> UUID:
        """Store `value` under `name` (unique), replacing an existing secret of that name in place. Returns its id."""
        self._check(c)
        # No `for update`: on Supabase the backend's role may read and delete Vault rows but not update them
        # directly (only through vault.update_secret). Callers serialise writes to one name (instagram_accounts
        # holds a per-Organization lock), and the name is unique, so a race fails rather than duplicates.
        row = c.execute("select id from vault.secrets where name = %s", (name,)).fetchone()
        if row:
            c.execute("select vault.update_secret(%s, %s, null, %s)", (row[0], value, description))
            return row[0]
        return c.execute("select vault.create_secret(%s, %s, %s)", (value, name, description)).fetchone()[0]

    def get(self, c: Connection, ref: UUID) -> str | None:
        self._check(c)
        row = c.execute("select decrypted_secret from vault.decrypted_secrets where id = %s", (ref,)).fetchone()
        return row[0] if row else None

    def delete(self, c: Connection, ref: UUID) -> None:
        self._check(c)
        c.execute("delete from vault.secrets where id = %s", (ref,))


secrets: SecretStore = VaultSecretStore()
