-- Organization profile (#16; GLOSSARY "Organization profile"): what the agents know about an Organization. It covers
-- what it does, its audience, home region, usual subjects and things never to say or promote. One row per
-- Organization; the jsonb `doc` is backend/app/schemas.py `OrgProfile`. Every Job stores a snapshot of it when it
-- starts, and every Brainstorm message reads the current one.
--
-- Same isolation as jobs (20261029140000_org_isolation.sql): `org_id` defaults to the transaction's Organization and
-- one RLS policy covers `wok_api`. Supabase's API roles get no grants. The API checks the Role (Admins edit, every
-- Member reads), and each change writes an `org.profile_updated` audit event in the same transaction.
create table org_profiles (
    org_id uuid primary key references orgs (id) on delete cascade
        default nullif(current_setting('app.org_id', true), '')::uuid,
    doc jsonb not null,
    updated_by uuid,
    updated_at timestamptz not null default now()
);

alter table org_profiles enable row level security;
create policy org_profiles_org on org_profiles for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
grant select, insert, update on org_profiles to wok_api;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table org_profiles from %I', r);
        end if;
    end loop;
end
$$;
