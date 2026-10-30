-- Instagram token refresh (#22; HARDENING PUB-3; docs/spec/v1.md pseudocode 2). A daily DBOS schedule
-- (backend/app/token_refresh.py) refreshes every long-lived token within 14 days of expiry. A token Instagram calls
-- invalid, or one that has already expired, marks its account `revoked` (needs reconnecting) and emails the
-- Organization's Admins. These columns are written only on the privileged path; `wok_api` keeps SELECT and its
-- disconnect-only UPDATE grant (20261029180000_instagram_accounts.sql).
alter table instagram_accounts add column token_refreshed_at timestamptz;  -- last successful refresh
alter table instagram_accounts add column revoked_at timestamptz;
alter table instagram_accounts add column revoked_reason text              -- shown to the Admin
    check (revoked_reason is null or length(revoked_reason) <= 300);
alter table instagram_accounts add column token_alerted_at timestamptz;    -- last "refresh keeps failing" email
