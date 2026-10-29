-- Private slides (#18; docs/HARDENING.md SEC-3). Rendered slides live in the Storage bucket `slides` only while
-- a publish is running, under `<org_id>/<job_id>/slide-NN.jpg`, and Instagram reads them through signed URLs
-- that expire in minutes (backend/app/services/image_host.py).
--
--   * The bucket is private: `/storage/v1/object/public/slides/...` answers 400/404, never the file.
--   * No policies on storage.objects for this bucket: RLS is on for storage.objects, so `anon` and
--     `authenticated` (the browser's roles) can't list, read, write or sign anything in it. Only the backend's
--     service key (host-only, bypasses RLS) uploads, signs and deletes.
--   * Only JPEGs up to 10 MB.
--
-- On a project where `slides` is already public (staging), this flips it to private; links handed out earlier
-- stop working, which is the point (Instagram already has its own copies). Deploy the backend that refuses public
-- buckets first: the old one flips the bucket back to public on its next publish.
--
-- Plain Postgres (tests, CI) has no `storage` schema: then this does nothing.
do $$
declare
    p record;
begin
    if to_regclass('storage.buckets') is null then
        return;
    end if;
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('slides', 'slides', false, 10485760, array['image/jpeg'])
    on conflict (id) do update
        set public = false, file_size_limit = excluded.file_size_limit,
            allowed_mime_types = excluded.allowed_mime_types;

    -- Anything that once opened this bucket to the browser roles (e.g. a dashboard "public read" policy).
    if to_regclass('storage.objects') is not null then
        for p in select policyname from pg_policies
                 where schemaname = 'storage' and tablename = 'objects'
                   and (coalesce(qual, '') || coalesce(with_check, '')) like '%''slides''%'
        loop
            execute format('drop policy %I on storage.objects', p.policyname);
        end loop;
    end if;
end
$$;
