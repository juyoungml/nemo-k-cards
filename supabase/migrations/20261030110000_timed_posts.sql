-- Timed Posts (#26; docs/spec/v1.md decision D6; GLOSSARY "Post", "Post on hold"). An Approver posts now or picks a
-- date and time (KST in the Admin, stored UTC in `scheduled_at`); until publishing starts they can cancel or
-- reschedule it. Three new states:
--
--   scheduled → queued (at `scheduled_at`) → … as before                    the publisher's workflow sleeps durably
--   scheduled → on_hold   the Deck was edited after approval, or the server missed the time by more than 6 hours
--   scheduled | on_hold → cancelled   by an Approver, or replaced by a new approval of the Deck (re-approval resumes
--                                     the hold as a new Post with the new version, at the same time if still ahead)
--
-- `on_hold` stays open (one open Post per Job), so nothing else can be approved past it by accident; approving the
-- Deck again replaces it. `cancelled` is final and posted nothing, so, like `failed`, it doesn't count against "this
-- version is already on that account".
alter table posts drop constraint if exists posts_state_check;
alter table posts add constraint posts_state_check check (state in (
    'scheduled', 'on_hold', 'queued', 'container_ready', 'publishing', 'published', 'failed', 'unknown', 'cancelled'));
alter table posts add constraint posts_cancelled_is_final check (state <> 'cancelled' or finished_at is not null);
alter table posts add constraint posts_scheduled_has_time check (state <> 'scheduled' or scheduled_at is not null);

alter table posts add column held_reason text check (held_reason is null or length(held_reason) <= 500);
alter table posts add column held_at timestamptz;
alter table posts add column cancelled_by uuid;      -- a person cancelled it (NULL: replaced by a re-approval)

drop index posts_once_per_version_and_account;
create unique index posts_once_per_version_and_account on posts (job_id, coalesce(deck_version, 1), ig_user_id)
    where mode = 'graph' and state not in ('failed', 'cancelled');
create index posts_scheduled_idx on posts (org_id, scheduled_at) where state in ('scheduled', 'on_hold');

-- The time can be changed until publishing starts; who approved, what, where and how still can't.
grant update (scheduled_at, held_reason, held_at, cancelled_by) on posts to wok_api;
