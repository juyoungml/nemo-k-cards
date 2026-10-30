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
# Like Docker's containerd image store: an image ID resolves only while some tag points at it.
ids_known() { cat "$D"/local/* 2>/dev/null | grep -qxF "$1"; }
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
			case $s in caddy | cloudflared) continue ;; esac
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
	run)
		# `compose run --rm --no-deps -T api python -m app.migration_check`: fails while $D/migrations-missing exists
		[ ! -f "$D/migrations-missing" ] || { echo "migration check: the database lacks 1 of 14 migrations" >&2; exit 1; }
		;;
	logs) [ ! -f "$D/quicktunnel.log" ] || cat "$D/quicktunnel.log" ;;
	config) ;;
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
	rm) [ -f "$D/local/$(key "$3")" ] || { echo "No such image: $3" >&2; exit 1; }; rm -f "$D/local/$(key "$3")" ;;
	esac
	;;
build)
	# docker build ... -t <ref> --label org.opencontainers.image.revision=<sha> ... -> id sha256:<api|admin>-<sha12>
	shift
	ref="" sha=""
	while [ $# -gt 0 ]; do
		case $1 in
		-t) ref=$2; shift ;;
		--label) sha=${2#*revision=}; shift ;;
		esac
		shift
	done
	case $ref in *admin*) kind=admin ;; *) kind=api ;; esac
	id=sha256:$kind-$(printf '%s' "$sha" | cut -c1-12)
	[ ! -f "$D/build-fails" ] || { echo "build failed" >&2; exit 1; }
	echo "$id" >"$D/local/$(key "$ref")"
	echo "$id" >>"$D/images"
	;;
builder) ;;
rmi) rm -f "$D/local/$(key "$2")" ;;
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

# --- build mode (WOK_SOURCE=build): the host builds from a git repo, only on `update-now` ---------------------
command -v git >/dev/null || { echo "git missing"; exit 1; }
R=$T/repo
git init -q -b main "$R"
commit() { git -C "$R" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1" && git -C "$R" rev-parse HEAD | cut -c1-12; }
c1=$(commit one)
wok down >/dev/null
rm -f "$T/home/state/paused" "$T/home/state/bad-images"
cat >>"$T/home/.env" <<EOF
WOK_SOURCE=build
WOK_GIT_URL=$R
NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co
EOF
BA=nemo-k-cards-api

# 11. the timer changes nothing in build mode
: >"$T/d/calls.log"
out=$(wok update)
check "build mode: timer does nothing" "$(grep -c 'docker build\|compose.* up ' "$T/d/calls.log" || true)" 0
check "build mode: timer says how to deploy" "$(printf '%s' "$out" | grep -c 'update-now')" 1

# 12. `up` on a fresh host builds the default ref and starts it
wok up >/dev/null
check "up builds api from main" "$(running api)" "sha256:api-$c1"
check "up builds admin from main" "$(running admin)" "sha256:admin-$c1"
check "admin gets the public Supabase URL as a build arg" "$(grep -c 'NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co' "$T/d/calls.log")" 1
check "no registry pull for api/admin in build mode" "$(grep -c "pull.* api\|pull.* admin" "$T/d/calls.log" || true)" 0

# 13. update-now builds the new commit, keeps the old one as wok-previous
c2=$(commit two)
wok update-now >/dev/null
check "update-now builds the new commit" "$(running api)" "sha256:api-$c2"
check "previous build kept" "$(cat "$T/d/local/$(key "$BA:wok-previous")")" "sha256:api-$c1"

# 14. a broken build: rolled back to the last good one
c3=$(commit three)
touch "$T/d/bad/$(key "sha256:api-$c3")"
rc=0
wok update-now >/dev/null || rc=$?
check "unhealthy build exits 1" "$rc" 1
check "rolled back to c2" "$(running api)" "sha256:api-$c2"

# 15. a failing build leaves the stack as is
touch "$T/d/build-fails"
commit four >/dev/null
rc=0
wok update-now >/dev/null || rc=$?
rm -f "$T/d/build-fails"
check "build failure exits 1" "$rc" 1
check "build failure leaves api running" "$(running api)" "sha256:api-$c2"

# 16. update-now <ref> deploys that commit; an unknown ref fails without touching anything
wok update-now "$c2" >/dev/null || true
wok update-now "$c1" >/dev/null
check "update-now <commit> deploys it" "$(running admin)" "sha256:admin-$c1"
rc=0
wok update-now no-such-branch >/dev/null || rc=$?
check "unknown ref exits 1" "$rc" 1
check "unknown ref leaves admin" "$(running admin)" "sha256:admin-$c1"
check "status names build mode" "$(wok status | grep -c 'build mode')" 1

# --- quick tunnel (WOK_TUNNEL=quick): no token, a random trycloudflare.com address ---------------------------
sed -i.bak '/^TUNNEL_TOKEN=/d' "$T/home/.env"
echo "WOK_TUNNEL=quick" >>"$T/home/.env"

# 17. the quicktunnel profile replaces the named tunnel, and `wok url` reads the address from its log
: >"$T/d/calls.log"
check "config names the quick tunnel" "$(wok config | grep -c 'tunnel: quicktunnel')" 1
check "quicktunnel profile passed to compose" "$(grep -c -- '--profile quicktunnel' "$T/d/calls.log" | tr -d ' ' | sed 's/^[1-9][0-9]*$/yes/')" yes
rc=0
wok url >/dev/null || rc=$?
check "url before the tunnel is up exits 1" "$rc" 1
printf '%s\n' 'INF Requesting new quick Tunnel on trycloudflare.com...' \
	'INF |  https://midi-don-dates-university.trycloudflare.com  |' >"$T/d/quicktunnel.log"
check "url prints the address" "$(bash "$WOK" url 2>/dev/null)" https://midi-don-dates-university.trycloudflare.com
check "url says ADMIN_URL is stale" "$(wok url | grep -c 'Set ADMIN_URL=https://midi-don-dates-university')" 1
echo "ADMIN_URL=https://midi-don-dates-university.trycloudflare.com" >>"$T/home/.env"
check "url quiet when ADMIN_URL matches" "$(wok url | grep -c 'Set ADMIN_URL' || true)" 0
check "status shows the address" "$(wok status | grep -c 'Address:  https://midi-don-dates-university')" 1

# --- migration check: the new API never starts against a database that lacks its migrations ------------------
c5=$(commit five)
touch "$T/d/migrations-missing"
: >"$T/d/calls.log"
rc=0
out=$(wok update-now) || rc=$?
check "missing migrations: deploy refused, exit 1" "$rc" 1
check "missing migrations: api not restarted" "$(running api)" "sha256:api-$c1"
check "missing migrations: no compose up" "$(grep -c 'compose.* up ' "$T/d/calls.log" || true)" 0
check "missing migrations: old image back under the tag" "$(cat "$T/d/local/$(key "$BA:staging")")" "sha256:api-$c1"
check "missing migrations: says what to run" "$(printf '%s' "$out" | grep -c 'supabase db push')" 1
check "missing migrations: recorded" "$(grep -c 'migrations-missing' "$T/home/state/history.log")" 1
check "missing migrations: not marked bad" "$(grep -cx "sha256:api-$c5" "$T/home/state/bad-images" 2>/dev/null || true)" 0
wok update-now --skip-migration-check >/dev/null
check "--skip-migration-check deploys anyway" "$(running api)" "sha256:api-$c5"
rm -f "$T/d/migrations-missing"
c6=$(commit six)
wok update-now >/dev/null
check "migrations applied: deploys" "$(running api)" "sha256:api-$c6"
check "check ran from the api service (refused run + this one; skipped once)" "$(grep -c 'compose.* run --rm --no-deps -T api python -m app.migration_check' "$T/d/calls.log")" 2

echo
if [ "$fails" -ne 0 ]; then
	echo "$fails failed"
	exit 1
fi
echo "all passed"
