-- Deck versions (#19; GLOSSARY "Deck version", docs/spec/v1.md `deck_versions`): one row per saved state of a Job's
-- Deck. Every edit in Review inserts the next version; approval pins exactly one (`ApproveRequest.deck_version`).
-- `doc` is backend/app/deck_versions.py `DeckVersion`: the whole Deck (slides, caption, hashtags), the rendered
-- slide files it is shown and posted with, and what changed. The Job document keeps the current version.
--
-- Version 1 is what the pipeline produced. It is written when the Deck is first edited or approved (a Job from
-- before this table has no rows and reads as version 1).
--
-- Same isolation as jobs (20261029140000_org_isolation.sql): `org_id` defaults to the transaction's Organization and
-- one RLS policy covers `wok_api`. Supabase's API roles get no grants. Insert-only for the API role, because a
-- version is never changed or deleted (an approved version must stay exactly what was approved). The insert check
-- also requires the Job to be visible to the transaction, which means the same Organization.
create table deck_versions (
    id uuid primary key default gen_random_uuid(),
    org_id uuid references orgs (id) on delete cascade
        default nullif(current_setting('app.org_id', true), '')::uuid,
    job_id text not null references jobs (id) on delete cascade,
    version integer not null check (version >= 1),
    edited_by uuid,                 -- null for version 1 (the pipeline)
    created_at timestamptz not null default now(),
    doc jsonb not null,
    constraint deck_versions_org_id_required check (org_id is not null),
    unique (job_id, version)
);
create index deck_versions_org_job_idx on deck_versions (org_id, job_id, version desc);

alter table deck_versions enable row level security;
create policy deck_versions_read on deck_versions for select to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
create policy deck_versions_insert on deck_versions for insert to wok_api
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid
                and exists (select 1 from jobs where jobs.id = job_id));  -- jobs RLS: only this Organization's
grant select, insert on deck_versions to wok_api;  -- no update, no delete

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table deck_versions from %I', r);
        end if;
    end loop;
end
$$;
