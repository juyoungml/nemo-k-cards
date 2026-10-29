# Supabase for identity, data, Instagram tokens and files

Organizations must never see each other's data, and each Organization's Instagram token can post publicly as that Organization. We put sign-in (Supabase Auth), the database (Postgres with shared tables and row-level security), token storage (Supabase Vault) and slide/photo files (Supabase Storage) on Supabase Cloud, because RLS policies can read the signed-in user directly and one managed service is what a small team can run safely. Decided 2026-10-29.

## Considered Options

- Better Auth or Logto for sign-in: built-in organizations and roles, but RLS would need the user ID passed through by hand, and Logto is one more service to run.
- Schema or database per Organization: stronger isolation, but every migration and backup multiplies.
- OpenBao or app-level encryption for tokens: more separation or portability, but more to operate or more key handling of our own.

## Consequences

- Moving off Supabase is a multi-week migration (users, policies, vault, files); the `Identity`, `SecretStore` and storage interfaces in `docs/spec/v1.md` keep the code ready for it.
- Supabase Auth has no organizations or roles; the `members` table holds them.
- RLS policies need their own tests: a wrong policy leaks data without any error.
