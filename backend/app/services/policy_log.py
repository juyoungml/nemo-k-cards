"""`openshell logs <sandbox> --source sandbox` → PolicyEvent rows for the Admin Policy Log (BACKEND §7).

Reads the CLI's OCSF shorthand lines, e.g.

    [1791351817.757] [sandbox] [OCSF ] [ocsf] HTTP:POST [MED] DENIED POST http://graph.instagram.com:443/v24.0/123/media
        [policy:instagram_guard engine:l7] [reason:L7_REQUEST deny POST ...]
    [1791351817.825] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(0) -> pastebin.com:443
        [reason:transparent_tcp_policy_denied]

Kept: every DENIED connection or request, and ALLOWED HTTP requests (what the agent actually read).
Connection-level ALLOWED lines are skipped — each is followed by its HTTP line — and so are allowed calls
to the inference host, which the provider adds and every stage makes many of.
"""

import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.schemas import PolicyEvent

KST = ZoneInfo("Asia/Seoul")
LINE = re.compile(r"^\[(?P<ts>[\d.]+)\] \[sandbox\] \[OCSF \] \[ocsf\] (?P<cls>NET|HTTP):(?P<act>[A-Z]+) "
                  r"\[(?P<sev>[A-Z]+)\] (?P<action>ALLOWED|DENIED|BLOCKED) (?P<rest>.*)$")
NET = re.compile(r"^(?P<binary>\S+?)\(\d+\) -> (?P<host>[^:\s]+):(?P<port>\d+)")
HTTP = re.compile(r"^(?P<method>[A-Z]+) (?P<url>\S+)")

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
INFERENCE_HOSTS = {"api.anthropic.com"}


def note_for(host: str, method: str | None, result: str) -> str | None:
    """Short human reading of a decision, shown next to it in the Policy Log."""
    if result != "policy_denied":
        return None
    if host.startswith("graph.") and method in WRITE_METHODS:
        return "Instagram write blocked — publishing is host-only"
    if "pastebin" in host or "webhook" in host or "requestbin" in host:
        return "exfiltration attempt"
    if method in WRITE_METHODS:
        return "write to a read-only endpoint"
    return "not allowed by policy"


def parse(text: str, sandbox: str, job_id: str | None = None) -> list[PolicyEvent]:
    events: list[PolicyEvent] = []
    binary_by_host: dict[str, str] = {}
    for line in text.splitlines():
        if not (m := LINE.match(line.strip())):
            continue
        time = datetime.fromtimestamp(float(m["ts"]), KST).strftime("%H:%M:%S")
        denied = m["action"] != "ALLOWED"
        if m["cls"] == "NET":
            if not (n := NET.match(m["rest"])):
                continue  # e.g. NET:REFUSE <host> — the NET:OPEN line that follows carries the binary
            binary_by_host[n["host"]] = n["binary"]
            if denied:
                events.append(PolicyEvent(time=time, sandbox=sandbox, binary=n["binary"], host=n["host"],
                                          request=f"CONNECT {n['host']}:{n['port']}", result="policy_denied",
                                          note=note_for(n["host"], None, "policy_denied"), job_id=job_id))
        elif h := HTTP.match(m["rest"]):
            u = urlparse(h["url"])
            host = u.hostname or ""
            path = u.path + (f"?{u.query}" if u.query else "")
            result = "policy_denied" if denied else "allowed"
            if result == "allowed" and host in INFERENCE_HOSTS:
                continue
            events.append(PolicyEvent(time=time, sandbox=sandbox, binary=binary_by_host.get(host, "—"),
                                      host=host, request=f"{h['method']} {path or '/'}", result=result,
                                      note=note_for(host, h["method"], result), job_id=job_id))
    return events
