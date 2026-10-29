-- Organization isolation (#13; docs/spec/v1.md "Isolation rule"). Every Organization-owned row carries its
-- Organization, and row-level security decides what the API sees:
--
--   * `wok_api` is the role the API's queries run as: no login, no RLS bypass, not the owner of any table.
--     The backend opens each transaction with `set local role wok_api` and
--     `set_config('app.org_id', <org>, true)` (app/store.py `tenant`); both end with the transaction, so a
--     pooled connection never carries one request's Organization into the next.
--   * Every policy compares against `nullif(current_setting('app.org_id', true), '')::uuid`. A transaction that
--     never set it sees no rows and can write none (NULL never equals anything): fail closed.
--   * Supabase's auto-generated API roles (anon, authenticated) get no policies and no grants on these tables:
--     the Admin talks to this backend, never to PostgREST.

-- ---------------------------------------------------------------- the API role
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'wok_api') then
        create role wok_api nologin nobypassrls;
    end if;
    -- The role that runs migrations (and that the backend connects as) must be able to `set role wok_api`.
    -- Superusers (local Postgres, CI) already can; Supabase's `postgres` is not a superuser.
    if not (select rolsuper from pg_roles where rolname = current_user) then
        execute format('grant wok_api to %I', current_user);
    end if;
end
$$;

grant usage on schema public to wok_api;

-- ---------------------------------------------------------------- org_id on Organization-owned tables
-- Rows written before this migration have no Organization. They are not guessed into one: they stay, invisible
-- to every Organization (NULL matches no policy) and unchangeable through the API. The NOT VALID check makes
-- every new or updated row carry an Organization without failing on those old rows. To keep old rows, an
-- operator assigns them as the table owner (`update jobs set org_id = '<org>' where org_id is null`), then runs
-- `alter table jobs validate constraint jobs_org_id_required` (same for the other two tables).
alter table jobs add column org_id uuid references orgs (id) on delete cascade;
alter table brainstorms add column org_id uuid references orgs (id) on delete cascade;
alter table policy_events add column org_id uuid references orgs (id) on delete cascade;

-- Inserts take the transaction's Organization, so the store never has to pass it (and can't pass another).
alter table jobs alter column org_id set default nullif(current_setting('app.org_id', true), '')::uuid;
alter table brainstorms alter column org_id set default nullif(current_setting('app.org_id', true), '')::uuid;
alter table policy_events alter column org_id set default nullif(current_setting('app.org_id', true), '')::uuid;

alter table jobs add constraint jobs_org_id_required check (org_id is not null) not valid;
alter table brainstorms add constraint brainstorms_org_id_required check (org_id is not null) not valid;
alter table policy_events add constraint policy_events_org_id_required check (org_id is not null) not valid;

drop index jobs_created_at_idx;
create index jobs_org_created_at_idx on jobs (org_id, created_at desc, id desc);
create index brainstorms_org_id_idx on brainstorms (org_id);
create index policy_events_org_id_idx on policy_events (org_id, id desc);

-- ---------------------------------------------------------------- policies (RLS is already enabled on all five)
create policy jobs_org on jobs for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
create policy brainstorms_org on brainstorms for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
create policy policy_events_org on policy_events for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);

-- Sign-in looks up the caller's own memberships before any Organization is chosen, with `app.user_id` set
-- (the verified token's subject) instead of `app.org_id`. Read-only for now: creating an Organization is
-- the one privileged write (app/orgs.py); managing Members comes with #14.
create policy members_read on members for select to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid
           or user_id = nullif(current_setting('app.user_id', true), '')::uuid);
create policy orgs_read on orgs for select to wok_api
    using (id = nullif(current_setting('app.org_id', true), '')::uuid
           or id in (select org_id from members
                     where user_id = nullif(current_setting('app.user_id', true), '')::uuid));

-- Least privilege: no deletes, and policy events are append-only.
grant select, insert, update on jobs, brainstorms to wok_api;
grant select, insert on policy_events to wok_api;
grant select on orgs, members to wok_api;

-- ---------------------------------------------------------------- Supabase's auto-generated API
-- Supabase grants anon and authenticated everything on tables in `public` by default. RLS without policies
-- already hides every row from them; revoking the grants as well means a policy added by mistake later still
-- can't expose a table, and the default-privilege change keeps future tables private unless granted on purpose.
-- Guarded: plain Postgres (CI, unit tests) has no such roles.
do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table jobs, brainstorms, policy_events, orgs, members from %I', r);
            execute format('revoke all on all sequences in schema public from %I', r);
            execute format('alter default privileges in schema public revoke all on tables from %I', r);
            execute format('alter default privileges in schema public revoke all on sequences from %I', r);
            execute format('alter default privileges in schema public revoke all on functions from %I', r);
        end if;
    end loop;
end
$$;
