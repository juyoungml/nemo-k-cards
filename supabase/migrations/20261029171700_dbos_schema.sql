-- Durable Jobs (#17, ADR 0002): the `dbos` schema holds DBOS's workflow checkpoints (status, inputs, each step's
-- output, the Job queue).
--
--   * DBOS creates its own tables in this schema and migrates them at launch (backend/app/pipeline/durable.py),
--     so their DDL always matches the installed DBOS version. This migration only creates the schema first, so
--     its privileges are ours: nobody but its owner (the role that runs migrations and that the backend connects
--     as) can use it.
--   * Its rows span Organizations (a workflow's inputs name the Organization and may hold a Brainstorm board), so
--     it is not reachable as `wok_api`: a tenant transaction can't read another Organization's checkpoints.
--     App data keeps its row-level security; DBOS's tables are system tables, like `store.system()`.
--   * Supabase's API (PostgREST, pg_graphql) only serves `public` and `graphql_public` (supabase/config.toml),
--     and its roles get no usage on this schema either, so the checkpoints are not exposed even if someone adds
--     the schema to the exposed list.
create schema if not exists dbos;

revoke all on schema dbos from public;
-- Functions are executable by PUBLIC by default; DBOS may create some. Keep them closed too.
alter default privileges in schema dbos revoke execute on functions from public;

do $$
declare
    r text;
begin
    foreach r in array array['wok_api', 'anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on schema dbos from %I', r);
            execute format('alter default privileges in schema dbos revoke all on tables from %I', r);
            execute format('alter default privileges in schema dbos revoke all on sequences from %I', r);
            execute format('alter default privileges in schema dbos revoke all on functions from %I', r);
        end if;
    end loop;
end
$$;
