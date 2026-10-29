-- TESTS ONLY. A stand-in for Supabase Vault's interface on plain Postgres (CI, Homebrew), loaded by conftest.py only
-- when the test database has no `supabase_vault` extension. It stores secrets in PLAIN TEXT: never apply it to a
-- real database. It has the same names and signatures the backend uses (app/secret_store.py), so the tests run
-- the production code path; the real extension (local Supabase) is tested when available.
create schema vault;
revoke all on schema vault from public;

create table vault.secrets (
    id uuid primary key default gen_random_uuid(),
    name text unique,
    description text not null default '',
    secret text not null,
    key_id uuid,
    nonce bytea,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create view vault.decrypted_secrets as
    select id, name, description, secret, secret as decrypted_secret, key_id, nonce, created_at, updated_at
    from vault.secrets;

create function vault.create_secret(new_secret text, new_name text default null, new_description text default '',
                                    new_key_id uuid default null) returns uuid
    language sql
as $$
    insert into vault.secrets (secret, name, description, key_id)
    values (new_secret, new_name, coalesce(new_description, ''), new_key_id) returning id
$$;

create function vault.update_secret(secret_id uuid, new_secret text default null, new_name text default null,
                                    new_description text default null, new_key_id uuid default null) returns void
    language sql
as $$
    update vault.secrets set secret = coalesce(new_secret, secret), name = coalesce(new_name, name),
        description = coalesce(new_description, description), key_id = coalesce(new_key_id, key_id),
        updated_at = now()
    where id = secret_id
$$;

revoke all on all tables in schema vault from public;
revoke all on all functions in schema vault from public;
