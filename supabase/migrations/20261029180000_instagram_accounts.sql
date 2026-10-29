-- Instagram accounts (#15; GLOSSARY: Instagram account, Pilot Organization). An Admin connects the Organization's
-- Instagram Business or Creator account through Instagram's OAuth (app/instagram_accounts.py). The long-lived token
-- lives in Supabase Vault (ADR 0001, spec D3); this table holds only a reference to it (`token_ref`, the
-- `vault.secrets` id), never the token.
--
--   * `orgs.is_pilot`: only Pilot Organizations may connect while our Meta app is in development mode (spec D8).
--     The API role can't change it: an operator sets it with `uv run python -m app.pilot on <org-id>` or SQL.
--   * `orgs.instagram_account_limit`: how many Instagram accounts the Organization may connect. NULL = the
--     server default (INSTAGRAM_ACCOUNTS_PER_ORG, 1); billing (#29) will write it from the plan.
--   * `instagram_accounts`: one row per connected account; readable by every Member, disconnectable by Admins.
--     Connecting (which writes the Vault secret) is a privileged step, like joining an Organization: the API role
--     can never write `token_ref`, so it can never point a row at another Organization's secret.
--   * `instagram_connect_states`: the one-time nonce behind each signed OAuth `state`.
--
-- Vault access stays with the connecting role (the privileged backend path). `wok_api` gets no usage on the
-- `vault` schema, and neither do `anon` or `authenticated`.

-- ---------------------------------------------------------------- Vault
-- Supabase (Cloud and the local CLI) ships the extension; plain Postgres (CI) doesn't, and the backend then
-- refuses to connect accounts (tests load a stand-in, backend/tests/vault_shim.sql).
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'supabase_vault') then
        create extension if not exists supabase_vault cascade;
    end if;
end
$$;

-- ---------------------------------------------------------------- orgs: Pilot flag and account limit
alter table orgs add column is_pilot boolean not null default false;
alter table orgs add column instagram_account_limit integer check (instagram_account_limit >= 0);
-- `wok_api` only has SELECT on orgs (20261029140000), so neither column can be changed through the API.

-- ---------------------------------------------------------------- instagram_accounts
create table instagram_accounts (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null default nullif(current_setting('app.org_id', true), '')::uuid
        references orgs (id) on delete cascade,
    ig_user_id text not null check (ig_user_id ~ '^[0-9]{1,32}$'),
    username text not null check (length(username) between 1 and 64),
    account_type text not null check (account_type in ('BUSINESS', 'MEDIA_CREATOR')),
    token_ref uuid,                    -- vault.secrets.id: the token itself is never in this table
    token_expires_at timestamptz,
    -- connected; expiring and revoked are for token refresh (#22); disconnected = an Admin removed it
    status text not null default 'connected'
        check (status in ('connected', 'expiring', 'revoked', 'disconnected')),
    connected_by uuid not null,
    connected_at timestamptz not null default now(),
    disconnected_at timestamptz,
    unique (org_id, ig_user_id),
    constraint instagram_accounts_token_iff_not_disconnected
        check ((status = 'disconnected') = (token_ref is null))
);
alter table instagram_accounts enable row level security;

-- Every Member sees the Organization's accounts (GLOSSARY "Viewer sees everything").
create policy instagram_accounts_read on instagram_accounts for select to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
-- An Admin may disconnect, and that is the only change the API role can make.
create policy instagram_accounts_admin_disconnect on instagram_accounts for update to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin')
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid and status = 'disconnected');
grant select on instagram_accounts to wok_api;
grant update (status, disconnected_at) on instagram_accounts to wok_api;

-- Disconnecting (or deleting the row, e.g. when the Organization is deleted) drops the token from Vault in the
-- same transaction. SECURITY DEFINER: the API role has no access to Vault; it only ever drops the secret of the
-- row it was allowed to change.
create function instagram_accounts_drop_token() returns trigger
    language plpgsql security definer
    set search_path = pg_catalog, public, pg_temp
as $$
declare
    ref uuid := old.token_ref;
begin
    if tg_op = 'UPDATE' then
        if new.status <> 'disconnected' or ref is null then
            return new;
        end if;
        new.token_ref := null;
        new.disconnected_at := coalesce(new.disconnected_at, now());
    end if;
    if ref is not null and to_regclass('vault.secrets') is not null then
        execute 'delete from vault.secrets where id = $1' using ref;
    end if;
    return case when tg_op = 'UPDATE' then new else old end;
end
$$;
revoke all on function instagram_accounts_drop_token() from public;
create trigger instagram_accounts_drop_token before update of status or delete on instagram_accounts
    for each row execute function instagram_accounts_drop_token();

-- ---------------------------------------------------------------- OAuth state nonces
-- `state` is HMAC-signed (org, user, nonce, expiry); the nonce must also be here, unused and unexpired, so a state
-- works once. Only the nonce's SHA-256 is stored. The Admin's own transaction inserts it (the policy checks they
-- are an Admin of that Organization); the callback, which has no session, consumes it as the privileged role.
create table instagram_connect_states (
    nonce_hash text primary key,
    org_id uuid not null default nullif(current_setting('app.org_id', true), '')::uuid
        references orgs (id) on delete cascade,
    user_id uuid not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    used_at timestamptz
);
alter table instagram_connect_states enable row level security;
create policy instagram_connect_states_admin_insert on instagram_connect_states for insert to wok_api
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid
                and user_id = nullif(current_setting('app.user_id', true), '')::uuid
                and wok_role(org_id) = 'admin');
grant insert on instagram_connect_states to wok_api;

-- ---------------------------------------------------------------- closed to Supabase's API roles and to wok_api
do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table instagram_accounts, instagram_connect_states from %I', r);
            execute format('revoke all on function instagram_accounts_drop_token() from %I', r);
        end if;
    end loop;
    -- Nothing in Vault is granted to these roles (Supabase grants it to postgres and service_role only); revoking
    -- schema usage makes sure. On Supabase the migration role doesn't own `vault`, so a revoke it may not issue
    -- is skipped: tests/test_instagram_connect.py checks `wok_api` really can't read or call anything there.
    if to_regnamespace('vault') is not null then
        foreach r in array array['wok_api', 'anon', 'authenticated'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                begin
                    execute format('revoke all on schema vault from %I', r);
                exception when insufficient_privilege then
                    raise notice 'could not revoke vault usage from %', r;
                end;
            end if;
        end loop;
    end if;
end
$$;
