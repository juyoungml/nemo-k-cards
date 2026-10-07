#!/usr/bin/env bash
# Check policies/openshell/agent-policy.yaml inside a real sandbox: what the agent may and may not do.
# Needs the agent image (scripts/openshell_setup.sh or the docker build in it). No API key needed.
#
#   scripts/openshell_probe.sh            # prints PASS/FAIL per check, then the parsed Policy Log rows
#
# Exit code is the number of failed checks.
set -uo pipefail
cd "$(dirname "$0")/.."

name="probe-$(head -c2 /dev/urandom | od -An -tx1 | tr -d ' \n')"
probe='
t(){ label=$1; shift; out=$("$@" 2>&1); echo "$label|$?|$(echo "$out" | tr "\n" " " | cut -c1-80)"; }
t id                 id -un
t claude             claude --version
t ig_get             curl -sS -o /dev/null -w %{http_code} https://graph.instagram.com/v24.0/me
t ig_post            curl -sS -o /dev/null -w %{http_code} -X POST https://graph.instagram.com/v24.0/123/media
t fb_delete          curl -sS -o /dev/null -w %{http_code} -X DELETE https://graph.facebook.com/v21.0/17952
t pastebin           curl -sS -o /dev/null -w %{http_code} -X POST https://pastebin.com/api/api_post.php
t event_page_curl    curl -sS -o /dev/null -w %{http_code} https://culture.seoul.go.kr/
t public_api         curl -sS -o /dev/null -w %{http_code} https://apis.data.go.kr/
t write_agent_dir    sh -c "echo x >> /sandbox/agent/CLAUDE.md"
t new_agent_file     sh -c "echo x > /sandbox/agent/injected.md"
t hk_read_input      sh -c "head -c 1 /hackathon/input/misc/K_CULTURE_OFFICIAL_FINAL.md >/dev/null && ls /hackathon/input | wc -l"
t hk_write_input     sh -c "echo x > /hackathon/input/injected.md"
t hk_write_output    sh -c "echo ok > /hackathon/output/probe.txt"
t hk_list_restricted sh -c "ls /hackathon/restricted >/dev/null"
t hk_read_restricted sh -c "head -c 1 /hackathon/restricted/README.md >/dev/null"
t hk_list_secrets    sh -c "ls /hackathon/secrets >/dev/null"
t hk_read_secrets    sh -c "head -c 1 /hackathon/secrets/service_key.env >/dev/null"
t write_out         sh -c "echo ok > /sandbox/out/probe.txt"
'

echo "creating sandbox $name ..."
out=$(openshell sandbox create --name "$name" --from whatsonkorea-agent:latest \
        --policy policies/openshell/agent-policy.yaml -- sh -c "$probe" 2>/dev/null | grep '|')
# The log reaches the gateway asynchronously: re-read until it stops growing.
logs=
for _ in $(seq 10); do
  prev=$logs
  logs=$(openshell logs "$name" --source sandbox -n 2000 --color never 2>/dev/null)
  [ -n "$logs" ] && [ "$logs" = "$prev" ] && break
  sleep 1
done
openshell sandbox delete "$name" >/dev/null 2>&1

fails=0
check(){  # label, description, test expression over $rc and $body
  local line rc body
  line=$(grep "^$1|" <<<"$out"); rc=$(cut -d'|' -f2 <<<"$line"); body=$(cut -d'|' -f3- <<<"$line" | sed 's/[[:space:]]*$//')
  if [ -n "$line" ] && eval "$3"; then echo "PASS  $2  ($body)"; else echo "FAIL  $2  (rc=$rc $body)"; fails=$((fails+1)); fi
}
check id              "runs as the image's sandbox user"           '[ "$body" = sandbox ]'
check claude          "claude binary runs"                          '[[ "$body" == *"Claude Code"* ]]'
check ig_get          "Instagram GET allowed (any HTTP status)"     '[[ "$body" =~ ^[1-5][0-9][0-9]$ ]] && [ "$body" != 403 ]'
check ig_post         "Instagram POST (publish) denied at L7"       '[ "$body" = 403 ]'
check fb_delete       "Graph DELETE denied at L7"                   '[ "$body" = 403 ]'
check pastebin        "pastebin (exfiltration) denied"              '[ "$rc" != 0 ] || [ "$body" = 403 ]'
check event_page_curl "event pages only for claude, not curl"       '[ "$rc" != 0 ] || [ "$body" = 403 ]'
check public_api      "public data API reachable (read-only)"       '[[ "$body" =~ ^[1-5][0-9][0-9]$ ]] && [ "$body" != 403 ]'
check write_agent_dir "agent instructions are read-only"            '[ "$rc" != 0 ]'
check new_agent_file  "no new files in the agent dir"               '[ "$rc" != 0 ]'
check hk_read_input      "/hackathon/input is readable"                 '[ "$rc" = 0 ] && [ "$body" -gt 0 ]'
check hk_write_input     "/hackathon/input is read-only"               '[ "$rc" != 0 ]'
check hk_write_output    "/hackathon/output is writable"               '[ "$rc" = 0 ]'
check hk_list_restricted "/hackathon/restricted can't be listed"        '[ "$rc" != 0 ]'
check hk_read_restricted "/hackathon/restricted can't be read"          '[ "$rc" != 0 ]'
check hk_list_secrets    "/hackathon/secrets can't be listed"           '[ "$rc" != 0 ]'
check hk_read_secrets    "/hackathon/secrets can't be read"             '[ "$rc" != 0 ]'
check write_out      "/sandbox/out is writable"                    '[ "$rc" = 0 ]'

echo
echo "Policy Log rows parsed from the sandbox log:"
(cd backend && uv run --quiet python -c '
import sys
from app.services.policy_log import parse
for e in parse(sys.stdin.read(), sys.argv[1]):
    print(f"  {e.time}  {e.result:<14} {e.binary:<16} {e.host:<22} {e.request:<36} {e.note or str()}")
' "$name") <<<"$logs"

echo
[ "$fails" = 0 ] && echo "all checks passed" || echo "$fails check(s) failed"
exit "$fails"
