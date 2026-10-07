"""OpenShell sandbox log → PolicyEvent. tests/data/openshell-probe.txt is a real `openshell logs --source sandbox`
capture (OpenShell 0.1.2, MicroVM driver) of curl probes against policies/openshell/agent-policy.yaml."""

from pathlib import Path

from app.pipeline.agent_runner import _remap
from app.services import policy_log

LOG = (Path(__file__).parent / "data/openshell-probe.txt").read_text()


def test_parses_denials_and_allowed_requests():
    events = policy_log.parse(LOG, "wok-probe2", "job1")
    got = [(e.host, e.request, e.result) for e in events]
    assert got == [
        ("graph.instagram.com", "GET /v24.0/me", "allowed"),
        ("graph.instagram.com", "POST /v24.0/123/media", "policy_denied"),
        ("graph.facebook.com", "DELETE /v21.0/17952", "policy_denied"),
        ("pastebin.com", "CONNECT pastebin.com:443", "policy_denied"),
        ("culture.seoul.go.kr", "CONNECT culture.seoul.go.kr:443", "policy_denied"),
    ]
    assert {e.binary for e in events} == {"/usr/bin/curl"}
    assert all(e.sandbox == "wok-probe2" and e.job_id == "job1" for e in events)


def test_notes_explain_the_demo_denials():
    notes = {e.request.split()[0] + " " + e.host: e.note for e in policy_log.parse(LOG, "sb")}
    assert notes["DELETE graph.facebook.com"].startswith("Instagram write blocked")
    assert notes["CONNECT pastebin.com"] == "exfiltration attempt"
    assert notes["GET graph.instagram.com"] is None


def test_ignores_non_ocsf_lines():
    assert policy_log.parse("[1.0] [sandbox] [INFO ] [x] hello\nnot a log line\n", "sb") == []


def test_remap_swaps_uploaded_paths_only():
    payload = {"slide_paths": ["/host/out/j1/slide-00.jpg", "keep"], "deck": {"title": "/host/out/j1/slide-00.jpg"}}
    out = _remap(payload, {"/host/out/j1/slide-00.jpg": "/tmp/input/j1/slide-00.jpg"})
    assert out == {"slide_paths": ["/tmp/input/j1/slide-00.jpg", "keep"], "deck": {"title": "/tmp/input/j1/slide-00.jpg"}}
