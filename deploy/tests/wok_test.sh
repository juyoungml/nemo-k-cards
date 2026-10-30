#!/usr/bin/env bash
# Tests for deploy/wok's updater logic against a fake `docker` (no Docker, no root, no network needed).
#   bash deploy/tests/wok_test.sh
# Needs bash >= 4 (macOS: brew install bash). The fake keeps a registry, local tags and containers in files.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WOK=$here/../wok
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/stack" "$T/home/state" "$T/d/registry" "$T/d/local" "$T/d/containers" "$T/d/bad"

cat >"$T/bin/docker" <<'EOF'
#!/usr/bin/env bash
# Fake docker: just enough of the CLI for wok.
set -euo pipefail
D=$FAKE_DOCKER
key() { printf '%s' "$1" | tr '/:' '__'; }
echo "docker $*" >>"$D/calls.log"
ids_known() { grep -qxsF "$1" "$D/images"; } # every image ever pulled stays until pruned
resolve() { # ref or id -> id
	if [ -f "$D/local/$(key "$1")" ]; then cat "$D/local/$(key "$1")"; return 0; fi
	if ids_known "$1"; then printf '%s\n' "$1"; return 0; fi
	return 1
}
svc_ref() { if [ "$1" = api ]; then echo "$API_IMAGE:$IMAGE_TAG"; else echo "$ADMIN_IMAGE:$IMAGE_TAG"; fi; }
case $1 in
compose)
	shift
	while [ $# -gt 0 ]; do
		case $1 in --project-directory | -f | --env-file | --profile) shift 2 ;; *) break ;; esac
	done
	sub=$1
	shift
	svcs=()
	for a in "$@"; do case $a in -* | [0-9]*) ;; *) svcs+=("$a") ;; esac; done
	[ ${#svcs[@]} -gt 0 ] || svcs=(api admin)
	case $sub in
	pull)
		for s in "${svcs[@]}"; do
			ref=$(svc_ref "$s")
			[ -f "$D/registry/$(key "$ref")" ] || { echo "manifest unknown" >&2; exit 1; }
			cp "$D/registry/$(key "$ref")" "$D/local/$(key "$ref")"
			cat "$D/registry/$(key "$ref")" >>"$D/images"
		done
		;;
	ps)
		if [ "${1:-}" = -q ]; then [ ! -f "$D/containers/${svcs[0]}" ] || echo "cid-${svcs[0]}"; else ls "$D/containers"; fi
		;;
	up)
		rc=0
		for s in "${svcs[@]}"; do
			id=$(resolve "$(svc_ref "$s")")
			echo "$id" >"$D/containers/$s"
			[ ! -f "$D/bad/$(key "$id")" ] || rc=1
		done
		exit $rc
		;;
	down) rm -f "$D"/containers/* ;;
	logs | config) ;;
	*) echo "fake docker: compose $sub?" >&2; exit 2 ;;
	esac
	;;
inspect) cat "$D/containers/${4#cid-}" ;;
image)
	case $2 in
	inspect)
		id=$(resolve "$5") || exit 1
		case $4 in *revision*) echo "rev-${id#sha256:}" ;; *) echo "$id" ;; esac
		;;
	prune) ;;
	esac
	;;
tag)
	id=$(resolve "$2")
	echo "$id" >"$D/local/$(key "$3")"
	;;
*) echo "fake docker: $1?" >&2; exit 2 ;;
esac
EOF
chmod +x "$T/bin/docker"

cat >"$T/home/.env" <<'EOF'
DATABASE_URL=postgresql://x
GHCR_USER=bot
GHCR_TOKEN="ghp_not_a_real_token"
TUNNEL_TOKEN=tunnel-secret # comment
WOK_HEALTH_TIMEOUT=5
SUPABASE_URL=https://example.supabase.co
EOF

WOK_USER=$(id -un)
export PATH="$T/bin:$PATH" FAKE_DOCKER=$T/d WOK_HOME=$T/home WOK_USER WOK_NO_SWITCH=1
A=ghcr.io/juyoungml/nemo-k-cards-api
M=ghcr.io/juyoungml/nemo-k-cards-admin
key() { printf '%s' "$1" | tr '/:' '__'; }
push() { echo "$2" >"$T/d/registry/$(key "$1:staging")"; }
running() { cat "$T/d/containers/$1"; }
fails=0
check() {
	if [ "$2" = "$3" ]; then echo "ok   $1"; else
		echo "FAIL $1: got '$2', want '$3'"
		fails=$((fails + 1))
	fi
}
wok() { bash "$WOK" "$@" 2>&1; }

# 1. first start: both images pulled and started
push $A sha256:a1
push $M sha256:m1
out=$(wok update)
check "first update starts api" "$(running api)" sha256:a1
check "first update starts admin" "$(running admin)" sha256:m1
check "tunnel profile on when TUNNEL_TOKEN is set" "$(grep -c -- '--profile tunnel' "$T/d/calls.log" | tr -d ' ' | sed 's/^[1-9][0-9]*$/yes/')" yes

# 2. api.env leaves out host-only secrets
check "api.env keeps app settings" "$(grep -c '^DATABASE_URL=' "$T/home/stack/api.env")" 1
check "api.env drops GHCR_/WOK_/TUNNEL_TOKEN" "$(grep -cE '^(GHCR_|WOK_|TUNNEL_TOKEN)' "$T/home/stack/api.env" || true)" 0
check "api.env is private" "$(stat -f '%Lp' "$T/home/stack/api.env" 2>/dev/null || stat -c '%a' "$T/home/stack/api.env")" 600
check "secrets never printed" "$(printf '%s' "$out" | grep -c 'ghp_not_a_real_token\|tunnel-secret' || true)" 0

# 3. unchanged tag: nothing restarts
: >"$T/d/calls.log"
out=$(wok update)
check "no change -> up to date" "$(printf '%s' "$out" | grep -c 'up to date')" 1
check "no change -> no up" "$(grep -c 'compose.* up ' "$T/d/calls.log" || true)" 0

# 4. tag moves for api only: only api restarts, previous image kept
push $A sha256:a2
: >"$T/d/calls.log"
wok update >/dev/null
check "moved tag updates api" "$(running api)" sha256:a2
check "admin untouched" "$(grep -c 'up .*admin' "$T/d/calls.log" || true)" 0
check "previous api kept as wok-previous" "$(cat "$T/d/local/$(key "$A:wok-previous")")" sha256:a1

# 5. a broken image: rolled back, remembered, exit code 1
push $A sha256:a3
touch "$T/d/bad/$(key sha256:a3)"
rc=0
out=$(wok update) || rc=$?
check "unhealthy update exits 1" "$rc" 1
check "rolled back to a2" "$(running api)" sha256:a2
check "local tag points back to a2" "$(cat "$T/d/local/$(key "$A:staging")")" sha256:a2
check "a3 remembered as bad" "$(grep -cx sha256:a3 "$T/home/state/bad-images")" 1

# 6. the same broken tag on the next run: skipped, no restart, exit 0
: >"$T/d/calls.log"
rc=0
out=$(wok update) || rc=$?
check "bad image skipped, exit 0" "$rc" 0
check "still on a2" "$(running api)" sha256:a2
check "no restart for a known-bad image" "$(grep -c 'compose.* up ' "$T/d/calls.log" || true)" 0

# 7. pause: tag moves but nothing changes; --force overrides; resume follows again
wok pause finals demo >/dev/null
push $A sha256:a4
out=$(wok update)
check "paused -> no update" "$(running api)" sha256:a2
check "paused message names the reason" "$(printf '%s' "$out" | grep -c 'reason=finals demo')" 1
check "status shows PAUSED" "$(wok status | grep -c PAUSED)" 1
wok update-now --force >/dev/null
check "update-now --force updates while paused" "$(running api)" sha256:a4
wok resume >/dev/null
check "resume clears pause" "$([ -f "$T/home/state/paused" ] && echo yes || echo no)" no

# 8. manual rollback to the previous image, and the current one is then skipped
wok rollback >/dev/null
check "rollback returns to a2" "$(running api)" sha256:a2
wok update >/dev/null
check "rolled-back image not re-pulled" "$(running api)" sha256:a2

# 9. `wok up` (e.g. install.sh re-run) while paused starts what is on the host and doesn't pull
wok pause >/dev/null
push $A sha256:a5
wok up >/dev/null
check "up while paused doesn't pull" "$(running api)" sha256:a2
wok resume >/dev/null

# 10. registry unreachable / tag missing: exit 1, stack untouched
rm -f "$T/d/registry/$(key "$M:staging")"
rc=0
wok update >/dev/null || rc=$?
check "pull failure exits 1" "$rc" 1
check "pull failure leaves api running" "$(running api)" sha256:a2

echo
if [ "$fails" -ne 0 ]; then
	echo "$fails failed"
	exit 1
fi
echo "all passed"
