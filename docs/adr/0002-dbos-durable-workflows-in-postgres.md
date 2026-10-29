# DBOS durable workflows inside our Postgres

Jobs and Posts must survive restarts without duplicate or phantom posts; today they run as background tasks in the API process and die on restart. We run the pipeline and publishing as DBOS workflows, a Python library that checkpoints each step in the same Postgres, chosen over Hatchet and Temporal so there is no separate workflow engine to deploy and upgrade. Decided 2026-10-29.

## Consequences

- Workflow code must be deterministic: every side effect (agent calls, rendering, Instagram API calls, emails) goes inside a step, or a resumed workflow repeats it.
- Scheduled Posts and the daily token refresh are DBOS scheduled workflows and queues, not a separate cron.
- Per-Organization concurrency limits use DBOS queues.
