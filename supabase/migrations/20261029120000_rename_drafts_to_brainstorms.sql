-- Glossary rename (#9): the Brainstorm session was called "Draft" in code. Rows and RLS carry over with the rename;
-- RLS is re-asserted so the table can never come out of this migration readable by the API roles.
alter table drafts rename to brainstorms;
alter table brainstorms enable row level security;
