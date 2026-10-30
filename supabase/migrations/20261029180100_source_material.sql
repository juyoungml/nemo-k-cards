-- Source material (#20; GLOSSARY "Source material", ADR 0003): links, files and notes an Organization attaches to a
-- Job or a Brainstorm. One row per piece; the jsonb `doc` is backend/app/schemas.py `Material` (its extracted, capped
-- text included). The file itself is private storage under `<org_id>/<material_id>/` (local disk, or the private
-- Storage bucket `materials`; backend/app/materials.py).
--
-- Same isolation as jobs (20261029140000_org_isolation.sql): `org_id` defaults to the transaction's Organization,
-- one RLS policy for `wok_api`, no grants for Supabase's API roles. Append-only for the API role: material is never
-- edited in place (a Job keeps pointing at exactly what it was made from), and there is no delete grant.
create table materials (
    id text primary key,
    org_id uuid references orgs (id) on delete cascade
        default nullif(current_setting('app.org_id', true), '')::uuid,
    created_at timestamptz not null default now(),
    doc jsonb not null,
    constraint materials_org_id_required check (org_id is not null)
);
create index materials_org_id_idx on materials (org_id, created_at desc);

alter table materials enable row level security;
create policy materials_org on materials for all to wok_api
    using (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    with check (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
grant select, insert on materials to wok_api;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on table materials from %I', r);
        end if;
    end loop;
end
$$;

-- The private bucket for files (MATERIAL_STORE=supabase). Like `slides` (20261029150100_private_slides.sql): not
-- public, no storage.objects policies, so only the backend's service key (host-only) reads and writes it.
-- PDFs, re-encoded JPEGs and text files up to 10 MB. Plain Postgres (tests, CI) has no `storage` schema.
do $$
declare
    p record;
begin
    if to_regclass('storage.buckets') is null then
        return;
    end if;
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('materials', 'materials', false, 10485760, array['application/pdf', 'image/jpeg', 'text/plain'])
    on conflict (id) do update
        set public = false, file_size_limit = excluded.file_size_limit,
            allowed_mime_types = excluded.allowed_mime_types;
    if to_regclass('storage.objects') is not null then
        for p in select policyname from pg_policies
                 where schemaname = 'storage' and tablename = 'objects'
                   and (coalesce(qual, '') || coalesce(with_check, '')) like '%''materials''%'
        loop
            execute format('drop policy %I on storage.objects', p.policyname);
        end loop;
    end if;
end
$$;
