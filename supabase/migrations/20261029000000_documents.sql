-- Today's documents (Job, Brainstorm Draft, PolicyEvent) as jsonb, one table per kind.
create table jobs (
    id text primary key,
    created_at timestamptz not null,
    doc jsonb not null
);
create index jobs_created_at_idx on jobs (created_at desc, id desc);

create table drafts (
    id text primary key,
    doc jsonb not null
);

create table policy_events (
    id bigint generated always as identity primary key,
    job_id text,
    doc jsonb not null
);

-- No policies yet: the anon and authenticated API roles see nothing; the backend (table owner) is unaffected.
alter table jobs enable row level security;
alter table drafts enable row level security;
alter table policy_events enable row level security;
