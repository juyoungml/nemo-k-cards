-- Posts (#21; GLOSSARY: Post; docs/spec/v1.md "Publish state machine", pseudocode 4). One publication of an approved
-- Deck to one Instagram account, with an honest state:
--
--   queued → container_ready (creation_id saved) → publishing → published | failed | unknown
--   unknown → published | container_ready (retry, same container) | failed   (the reconciler, or a person)
--
-- A Post is its own record: the Job no longer carries publish fields (`published_url`, `publish_progress` move
-- here; existing Job documents are migrated below). The publisher (app/pipeline/publishing.py) runs each Post as a
-- DBOS workflow and writes every transition here *before* the Instagram call it guards.
--
-- Duplicates are refused by the database, not only by the code:
--   * one open Post per Job (`finished_at is null`): a double-clicked Approve gets a 409, never a second publish;
--   * a Deck version is published at most once to one Instagram account (`mode = 'graph'`, any state but
--     `failed`): a retry can only finish the same Post, whose saved `creation_id` Instagram publishes once.
--     `deck_version` is NULL until #19 pins versions; NULL counts as version 1, as #19 treats older Jobs.

-- A Post's Job and account must belong to the Post's own Organization (composite foreign keys).
alter table jobs add constraint jobs_id_org_key unique (id, org_id);
alter table instagram_accounts add constraint instagram_accounts_id_org_key unique (id, org_id);

create table posts (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null default nullif(current_setting('app.org_id', true), '')::uuid
        references orgs (id) on delete cascade,
    job_id text not null,
    deck_version integer,                      -- #19 pins one; NULL = the Deck as it was (version 1)
    content_hash text,                         -- the Safeguard hash of exactly what was (to be) posted
    instagram_account_id uuid,                 -- NULL only for mock Posts and Posts from before #21
    ig_user_id text,                           -- snapshot: the account's Instagram id when approved
    username text,                             -- snapshot, for history after a disconnect
    mode text not null check (mode in ('mock', 'dryrun', 'graph')),
    state text not null default 'queued'
        check (state in ('queued', 'container_ready', 'publishing', 'published', 'failed', 'unknown')),
    caption text,                              -- the approved edit, if any (NULL = the Deck's caption)
    creation_id text,                          -- the carousel container: the idempotency key of the publish
    container_created_at timestamptz,          -- containers expire 24 h after creation
    media_id text,
    permalink text,
    attempts integer not null default 0,       -- media_publish calls
    reconcile_attempts integer not null default 0,
    runs integer not null default 1,           -- workflows that drove it (a restart may need a fresh one)
    progress jsonb,                            -- live sub-steps for the Admin (upload, containers, ...)
    last_error text,
    approved_by uuid not null,
    approved_by_email text,
    scheduled_at timestamptz,                  -- #26 (Timed Posts); NULL = now
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    publishing_started_at timestamptz,
    unknown_since timestamptz,
    next_check_at timestamptz,                 -- unknown: when the reconciler looks next; NULL = waiting for a person
    alerted_at timestamptz,                    -- unknown for 30 min: a person was told
    published_at timestamptz,
    finished_at timestamptz,                   -- set once nothing more will happen to it
    resolved_by uuid,                          -- a person settled an unknown Post by hand
    legacy boolean not null default false,     -- made by this migration from a Job published before #21
    foreign key (job_id, org_id) references jobs (id, org_id) on delete cascade,
    foreign key (instagram_account_id, org_id) references instagram_accounts (id, org_id),
    constraint posts_account_unless_mock check (mode = 'mock' or instagram_account_id is not null or legacy),
    constraint posts_published_is_final check (state <> 'published' or finished_at is not null),
    constraint posts_failed_is_final check (state <> 'failed' or finished_at is not null),
    constraint posts_publishing_has_container check (state not in ('publishing', 'unknown')
                                                     or creation_id is not null or mode = 'mock' or legacy)
);
create unique index posts_one_open_per_job on posts (job_id) where finished_at is null;
create unique index posts_once_per_version_and_account on posts (job_id, coalesce(deck_version, 1), ig_user_id)
    where mode = 'graph' and state <> 'failed';
create index posts_org_created_idx on posts (org_id, created_at desc);
create index posts_open_idx on posts (state) where finished_at is null;

alter table posts enable row level security;
create policy posts_org on posts for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
-- No delete: a Post is history. What it publishes (Job, account, mode, approver) can't change after approval.
grant select, insert on posts to wok_api;
grant update (deck_version, content_hash, state, caption, creation_id, container_created_at, media_id, permalink,
              attempts, reconcile_attempts, runs, progress, last_error, updated_at, publishing_started_at,
              unknown_since, next_check_at, alerted_at, published_at, finished_at, resolved_by)
    on posts to wok_api;

-- ---------------------------------------------------------------- one Organization per Instagram account
-- Two Organizations posting to the same Instagram account would mean two approval trails and duplicate posts.
-- Connecting refuses it (app/instagram_accounts.py); this index is the backstop for a race between two
-- Organizations connecting at once. Any existing duplicates keep the earliest connection; the later ones are
-- disconnected (the trigger drops their tokens from Vault).
update instagram_accounts a set status = 'disconnected', disconnected_at = now()
where status <> 'disconnected' and exists (
    select 1 from instagram_accounts b
    where b.ig_user_id = a.ig_user_id and b.status <> 'disconnected' and b.id <> a.id
      and (b.connected_at, b.id) < (a.connected_at, a.id));
create unique index instagram_accounts_one_org_per_account on instagram_accounts (ig_user_id)
    where status <> 'disconnected';

-- ---------------------------------------------------------------- Jobs: publish fields move to Posts
-- Published Jobs get a published Post (the env account had no row: account NULL). A Job caught mid-publish by
-- this migration gets an `unknown` Post a person resolves after checking Instagram (it may have gone live).
insert into posts (legacy, org_id, job_id, mode, state, permalink, attempts, approved_by, created_at, updated_at,
                   publishing_started_at, published_at, finished_at, unknown_since, alerted_at, last_error, progress)
select true, j.org_id, j.id,
       case when coalesce(j.doc->>'published_url', '') like '%/p/MOCK%' then 'mock'
            else coalesce(j.doc->'publish_progress'->>'mode', 'graph') end,
       case when j.doc->>'status' = 'PUBLISHED' then 'published' else 'unknown' end,
       nullif(j.doc->>'published_url', ''),
       case when j.doc->>'status' = 'PUBLISHED' then 1 else 0 end,
       '00000000-0000-0000-0000-000000000000',
       coalesce((j.doc->'publish_progress'->>'started_at')::timestamptz, j.created_at),
       now(),
       (j.doc->'publish_progress'->>'started_at')::timestamptz,
       case when j.doc->>'status' = 'PUBLISHED'
            then coalesce((j.doc->'publish_progress'->>'finished_at')::timestamptz, j.created_at) end,
       case when j.doc->>'status' = 'PUBLISHED'
            then coalesce((j.doc->'publish_progress'->>'finished_at')::timestamptz, j.created_at) end,
       case when j.doc->>'status' = 'PUBLISHING' then now() end,
       case when j.doc->>'status' = 'PUBLISHING' then now() end,
       case when j.doc->>'status' = 'PUBLISHING'
            then 'Interrupted while publishing, before Posts existed. Check Instagram, then resolve it.' end,
       j.doc->'publish_progress'
from jobs j
where j.org_id is not null and j.doc->>'status' in ('PUBLISHED', 'PUBLISHING');

update jobs set doc = doc - 'published_url' - 'publish_progress'
where doc ? 'published_url' or doc ? 'publish_progress';

-- ---------------------------------------------------------------- closed to Supabase's API roles
do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table posts from %I', r);
        end if;
    end loop;
end
$$;
