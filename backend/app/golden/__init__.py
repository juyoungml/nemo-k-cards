"""Golden set (HARDENING AGT-3): fixed requests with expected properties, checked on every prompt or model change.

    uv run python -m app.golden                                  # replay: check recorded outputs (free)
    uv run python -m app.golden --live --case seongsu-popups     # live: run the real pipeline (costs tokens)
    uv run python -m app.golden --live --case seongsu-popups --record   # ... and save it as the new recording

Cases live in `backend/golden/cases.json`, recordings in `backend/golden/recordings/<case_id>.json`.
`cases.py` holds the formats, `checks.py` the per-Deck checks, `live.py` the pipeline run.
"""
