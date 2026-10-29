-- Organizations and Members (GLOSSARY; docs/spec/v1.md data model). Supabase Auth has no organizations or
-- roles, so `members` holds them; `user_id` is Supabase Auth's user id (auth.uid()).
create table orgs (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(name) between 1 and 100),
    plan text,                                   -- set by billing (later)
    time_zone text not null default 'Asia/Seoul',
    created_by uuid not null,
    created_at timestamptz not null default now()
);

create table members (
    org_id uuid not null references orgs (id) on delete cascade,
    user_id uuid not null,
    role text not null check (role in ('viewer', 'editor', 'approver', 'admin')),
    invited_by uuid,
    created_at timestamptz not null default now(),
    primary key (org_id, user_id)
);
create index members_user_id_idx on members (user_id);

-- Deleting a person in Supabase Auth removes their memberships. Guarded because the `auth` schema exists only
-- on Supabase (local CLI and Cloud), not in the plain Postgres that CI and unit tests use.
do $$
begin
    if to_regclass('auth.users') is not null then
        alter table members add constraint members_user_id_fkey
            foreign key (user_id) references auth.users (id) on delete cascade;
    end if;
end
$$;

-- No policies yet: Supabase's auto-generated API (anon/authenticated roles) sees nothing. The backend (table
-- owner) is unaffected; per-Organization policies come with Organization isolation (#13).
alter table orgs enable row level security;
alter table members enable row level security;
