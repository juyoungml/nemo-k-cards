-- Members, Roles and Overrides (#14; GLOSSARY: Member, Role, Override). Builds on Organization isolation
-- (20261029140000): the API's queries run as `wok_api` with `app.org_id` and, from now on, `app.user_id` (the
-- verified caller) set per transaction. This migration adds:
--
--   * `wok_role(org)`: the caller's Role in an Organization, for policies that depend on it.
--   * Admin-only write policies on `members` (change a Role, remove a Member) and on `invitations`.
--   * `invitations`: pending invitations by email, each with a one-time token stored only as a hash.
--   * `audit_events`: the append-only audit log (approvals, Overrides, rejections, edits, Member changes).
--   * A last-Admin guard: an Organization never ends up without an Admin.
--
-- Joining an Organization from an invitation runs as the connecting role (app/members.py `accept`), like
-- creating one: the invitee is not a Member yet, and the invitation token is the only thing that says which
-- Organization they may join, so there is no `app.org_id` a policy could scope it to beforehand.

-- ---------------------------------------------------------------- the caller's Role
-- SECURITY DEFINER so a policy on `members` can read `members` without recursing into its own policies.
-- It answers only for the user in `app.user_id` (set by the backend from the verified token), never for an
-- arbitrary user, and returns NULL for a non-Member.
create function wok_role(org uuid) returns text
    language sql stable security definer
    set search_path = pg_catalog, public, pg_temp
as $$
    select m.role from public.members m
    where m.org_id = org and m.user_id = nullif(current_setting('app.user_id', true), '')::uuid
$$;
revoke all on function wok_role(uuid) from public;
grant execute on function wok_role(uuid) to wok_api;

-- ---------------------------------------------------------------- members: Admin-only writes
alter table members add column email text;  -- as given at sign-in when the person joined; for display only

create policy members_admin_update on members for update to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin')
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
create policy members_admin_delete on members for delete to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin');
-- Only the Role can change; who and where are fixed. Inserts stay privileged (creating or joining).
grant update (role) on members to wok_api;
grant delete on members to wok_api;

-- The last Admin can be neither demoted nor removed. The API checks this first, under a per-Organization
-- lock (app/members.py), and answers 409; this trigger is the backstop for any other path. It runs only for
-- statements aimed at `members` itself (trigger depth 1): deleting the whole Organization, or deleting the
-- person in Supabase Auth, cascades here at depth 2 and must not be blocked.
create function members_keep_an_admin() returns trigger
    language plpgsql security definer
    set search_path = pg_catalog, public, pg_temp
as $$
begin
    if pg_trigger_depth() = 1 and old.role = 'admin'
       and exists (select 1 from public.orgs where id = old.org_id)
       and not exists (select 1 from public.members where org_id = old.org_id and role = 'admin') then
        raise exception 'an Organization needs at least one Admin' using errcode = '23514';
    end if;
    return null;
end
$$;
revoke all on function members_keep_an_admin() from public;
create trigger members_keep_an_admin after update of role or delete on members
    for each row execute function members_keep_an_admin();

-- ---------------------------------------------------------------- invitations
-- An Admin invites an email address with a Role. The link carries a random token; only its SHA-256 is stored.
-- Accepting needs that token AND a signed-in account with the same email address, so neither a leaked link
-- nor an account registered under someone else's address is enough on its own.
create table invitations (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null default nullif(current_setting('app.org_id', true), '')::uuid
        references orgs (id) on delete cascade,
    email text not null check (email = lower(email) and length(email) between 3 and 320),
    role text not null check (role in ('viewer', 'editor', 'approver', 'admin')),
    token_hash text not null unique,
    invited_by uuid not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null default now() + interval '7 days',
    accepted_at timestamptz,
    accepted_by uuid,
    revoked_at timestamptz
);
-- At most one open invitation per address and Organization (inviting again replaces it).
create unique index invitations_open_idx on invitations (org_id, email)
    where accepted_at is null and revoked_at is null;
alter table invitations enable row level security;

create policy invitations_admin_read on invitations for select to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin');
create policy invitations_admin_insert on invitations for insert to wok_api
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin'
                and invited_by = nullif(current_setting('app.user_id', true), '')::uuid);
create policy invitations_admin_revoke on invitations for update to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin')
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
grant select, insert on invitations to wok_api;
grant update (revoked_at) on invitations to wok_api;  -- revoking is the only change an Admin makes

-- ---------------------------------------------------------------- audit_events (append-only)
create table audit_events (
    id bigint generated always as identity primary key,
    org_id uuid not null default nullif(current_setting('app.org_id', true), '')::uuid
        references orgs (id) on delete cascade,
    actor uuid not null default nullif(current_setting('app.user_id', true), '')::uuid,
    actor_email text,
    action text not null,
    target text,
    detail jsonb not null default '{}'::jsonb,
    at timestamptz not null default now()
);
create index audit_events_org_idx on audit_events (org_id, id desc);
alter table audit_events enable row level security;

-- Any Member writes events for their own actions in their own Organization; only Admins read them.
create policy audit_events_insert on audit_events for insert to wok_api
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid
                and actor = nullif(current_setting('app.user_id', true), '')::uuid
                and wok_role(org_id) is not null);
create policy audit_events_admin_read on audit_events for select to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid and wok_role(org_id) = 'admin');
grant select, insert on audit_events to wok_api;  -- no update, no delete

-- Append-only for every role, the table owner included: an event can't be changed or deleted. The one
-- exception is deleting the whole Organization, whose rows cascade away with it.
create function audit_events_append_only() returns trigger
    language plpgsql
    set search_path = pg_catalog, public, pg_temp
as $$
begin
    if tg_op = 'DELETE' then
        if not exists (select 1 from public.orgs where id = old.org_id) then
            return old;
        end if;
    end if;
    raise exception 'audit_events is append-only' using errcode = '42501';
end
$$;
revoke all on function audit_events_append_only() from public;
create trigger audit_events_append_only before update or delete on audit_events
    for each row execute function audit_events_append_only();
create trigger audit_events_no_truncate before truncate on audit_events
    for each statement execute function audit_events_append_only();

-- ---------------------------------------------------------------- Supabase's auto-generated API
do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table invitations, audit_events from %I', r);
            execute format('revoke all on function wok_role(uuid), members_keep_an_admin(), '
                           'audit_events_append_only() from %I', r);
        end if;
    end loop;
end
$$;
