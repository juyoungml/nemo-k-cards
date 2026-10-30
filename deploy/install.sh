#!/usr/bin/env bash
# Bootstrap (or re-apply) the What's On Korea stack on any Linux host with Docker. Idempotent: safe to re-run.
#
#   sudo bash deploy/install.sh               # from a checkout: installs this checkout's deploy/ files
#   curl -fsSLO https://raw.githubusercontent.com/juyoungml/nemo-k-cards/main/deploy/install.sh
#   sudo bash install.sh --ref <commit>       # without a checkout: fetches deploy/ at that commit
#   bash deploy/install.sh --dry-run          # print what it would do, change nothing (no root needed)
#
# What it does: Docker Engine + Compose (installs them on Debian/Ubuntu if missing), a service user, the
# $WOK_HOME layout, the one env file (created from env.example on the first run; you fill it, then re-run),
# the registry login, the `wok` command, the updater timer, and it starts the stack. See deploy/README.md.
# Nothing here is specific to one server: every host value lives in $WOK_HOME/.env.
set -euo pipefail

WOK_HOME=${WOK_HOME:-/opt/wok}
WOK_USER=${WOK_USER:-wok}
REPO=${WOK_REPO:-juyoungml/nemo-k-cards}
REF=""
DRY_RUN=0
START=1

# Compose >= 2.24.4: `!reset` in compose.server.yml and `env_file: {path, required}` in compose.yml.
COMPOSE_MIN=2.24.4
# Installed only when Docker is there but its Compose plugin is missing or older than COMPOSE_MIN.
COMPOSE_PIN=v5.6.1
COMPOSE_SHA256_x86_64=47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0
COMPOSE_SHA256_aarch64=42cfa75f51ad0ccf290bad37899e0038a8c10a0069e85233ecc1d53e3ff1347d

FILES="compose.yml compose.server.yml Caddyfile env.example wok systemd/wok-update.service systemd/wok-update.timer"

usage() {
	cat <<'EOF'
Usage: sudo bash install.sh [--dry-run] [--ref <git ref>] [--no-start]

  --dry-run    Print every action instead of doing it. Read-only checks still run. No root needed.
  --ref REF    Fetch deploy/ files from github.com/$WOK_REPO at REF (commit, tag or branch) instead of
               using the checkout this script sits in. Default without a checkout: main.
  --no-start   Set everything up but don't start the stack.

Environment: WOK_HOME (default /opt/wok), WOK_USER (default wok), WOK_REPO (default juyoungml/nemo-k-cards).
EOF
}

while [ $# -gt 0 ]; do
	case $1 in
	--dry-run) DRY_RUN=1 ;;
	--ref)
		REF=${2:?--ref needs a value}
		shift
		;;
	--no-start) START=0 ;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		usage >&2
		exit 2
		;;
	esac
	shift
done

ENV_FILE=$WOK_HOME/.env
STACK=$WOK_HOME/stack

say() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
	printf 'ERROR: %s\n' "$*" >&2
	exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }
# Every change goes through run (or is guarded by DRY_RUN), so --dry-run prints it instead.
run() {
	if [ "$DRY_RUN" = 1 ]; then
		printf '  + %s\n' "$*"
	else
		"$@"
	fi
}
# write_file <mode> <owner:group> <path>  (content on stdin)
write_file() {
	if [ "$DRY_RUN" = 1 ]; then
		printf '  + write %s (mode %s, %s):\n' "$3" "$1" "$2"
		sed 's/^/      | /'
		return 0
	fi
	local tmp
	tmp=$(mktemp)
	cat >"$tmp"
	install -m "$1" -o "${2%%:*}" -g "${2##*:}" "$tmp" "$3"
	rm -f "$tmp"
}
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# Same parser as wok: a compose env file is not shell, so it is read, never sourced. Values are never printed.
env_get() {
	local key=$1 line val
	line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1) || true
	val=${line#*=}
	val=${val%$'\r'}
	case $val in
	\"*\") val=${val#\"} val=${val%\"} ;;
	\'*\') val=${val#\'} val=${val%\'} ;;
	*) val=$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//') ;;
	esac
	printf '%s' "$val"
}

# --- 1. preflight -----------------------------------------------------------------------------------------------
preflight() {
	[ "$DRY_RUN" = 1 ] && say "DRY RUN: nothing is changed; commands are printed with '+'."
	if [ "$(uname -s)" != Linux ]; then
		[ "$DRY_RUN" = 1 ] || die "this installs a Linux server; $(uname -s) is not supported"
		warn "not Linux ($(uname -s)): OS-specific checks are skipped in this dry run"
	fi
	if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" != 1 ]; then die "run as root (sudo bash $0 ...)"; fi
	OS_ID="" OS_CODENAME=""
	if [ -r /etc/os-release ]; then
		# shellcheck source=/dev/null
		. /etc/os-release
		case "${ID:-}" in
		ubuntu | debian) OS_ID=$ID OS_CODENAME=${VERSION_CODENAME:-} ;;
		*)
			case " ${ID_LIKE:-} " in
			*" ubuntu "*) OS_ID=ubuntu OS_CODENAME=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}} ;;
			*" debian "*) OS_ID=debian OS_CODENAME=${VERSION_CODENAME:-} ;;
			esac
			;;
		esac
		say "host: ${PRETTY_NAME:-unknown Linux}, $(uname -m)"
	fi
	if [ "$DRY_RUN" != 1 ]; then
		for t in curl install sed grep sort mktemp; do have "$t" || die "missing $t"; done
	fi
}

# --- 2. Docker Engine + Compose ---------------------------------------------------------------------------------
install_docker_apt() {
	say "installing Docker Engine from Docker's apt repository ($OS_ID $OS_CODENAME)"
	[ -n "$OS_CODENAME" ] || die "can't tell the $OS_ID release codename; install Docker by hand"
	run apt-get update -qq
	run apt-get install -y -qq ca-certificates curl
	run install -m 0755 -d /etc/apt/keyrings
	run curl -fsSL "https://download.docker.com/linux/$OS_ID/gpg" -o /etc/apt/keyrings/docker.asc
	run chmod a+r /etc/apt/keyrings/docker.asc
	local arch
	arch=$(dpkg --print-architecture 2>/dev/null || echo amd64)
	printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
		"$arch" "$OS_ID" "$OS_CODENAME" | write_file 644 root:root /etc/apt/sources.list.d/docker.list
	run apt-get update -qq
	run apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_compose_plugin() {
	local arch sha url dir=/usr/local/lib/docker/cli-plugins tmp
	arch=$(uname -m)
	case $arch in
	x86_64 | amd64) arch=x86_64 sha=$COMPOSE_SHA256_x86_64 ;;
	aarch64 | arm64) arch=aarch64 sha=$COMPOSE_SHA256_aarch64 ;;
	*) die "no pinned Compose build for $arch; install the Compose plugin >= $COMPOSE_MIN by hand" ;;
	esac
	url=https://github.com/docker/compose/releases/download/$COMPOSE_PIN/docker-compose-linux-$arch
	say "installing Docker Compose $COMPOSE_PIN to $dir (checksum-verified)"
	if [ "$DRY_RUN" = 1 ]; then
		printf '  + curl -fsSL %s, check sha256 %s, install to %s/docker-compose\n' "$url" "$sha" "$dir"
		return 0
	fi
	tmp=$(mktemp)
	curl -fsSL "$url" -o "$tmp"
	echo "$sha  $tmp" | sha256sum -c --quiet - || {
		rm -f "$tmp"
		die "Compose download failed its checksum"
	}
	install -d -m 755 "$dir"
	install -m 755 "$tmp" "$dir/docker-compose"
	rm -f "$tmp"
}

ensure_docker() {
	if ! have docker; then
		if [ -n "$OS_ID" ]; then
			install_docker_apt
		elif [ "$DRY_RUN" = 1 ]; then
			warn "docker is not installed here; on a server this step installs it (Debian/Ubuntu) or stops"
		else
			die "Docker isn't installed and this isn't Debian/Ubuntu. Install Docker Engine (https://docs.docker.com/engine/install/) and re-run."
		fi
	else
		say "docker: $(docker --version 2>/dev/null || echo present)"
	fi
	if have systemctl && [ -d /run/systemd/system ]; then
		run systemctl enable --now docker
	fi
	local v
	v=$(docker compose version --short 2>/dev/null || true)
	v=${v#v}
	if [ -n "$v" ] && version_ge "$v" "$COMPOSE_MIN"; then
		say "docker compose: $v"
	else
		say "docker compose: ${v:-missing} (need >= $COMPOSE_MIN)"
		install_compose_plugin
		if [ "$DRY_RUN" != 1 ]; then
			v=$(docker compose version --short 2>/dev/null || true)
			version_ge "${v#v}" "$COMPOSE_MIN" || die "docker compose is still ${v:-missing}; a newer plugin earlier in Docker's plugin path? See 'docker info'"
		fi
	fi
}

# --- 3. service user and layout ---------------------------------------------------------------------------------
ensure_user() {
	if id "$WOK_USER" >/dev/null 2>&1; then
		say "service user $WOK_USER exists"
	else
		say "creating service user $WOK_USER (no login shell, home $WOK_HOME)"
		run useradd --system --user-group --home-dir "$WOK_HOME" --no-create-home --shell /usr/sbin/nologin "$WOK_USER"
	fi
	# The docker group is root-equivalent on this host; only this account and admins get it.
	run usermod -aG docker "$WOK_USER"
	if getent group systemd-journal >/dev/null 2>&1; then run usermod -aG systemd-journal "$WOK_USER"; fi
	run install -d -m 750 -o "$WOK_USER" -g "$WOK_USER" "$WOK_HOME" "$STACK" "$WOK_HOME/state"
	run install -d -m 700 -o "$WOK_USER" -g "$WOK_USER" "$WOK_HOME/.docker"
}

# --- 4. deploy files --------------------------------------------------------------------------------------------
SRC=""
fetch_files() {
	local here=""
	if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); fi
	if [ -z "$REF" ] && [ -n "$here" ] && [ -f "$here/compose.yml" ] && [ -f "$here/wok" ]; then
		SRC=$here
		say "deploy files: this checkout ($SRC$(have git && git -C "$SRC" rev-parse --short HEAD 2>/dev/null | sed 's/^/ @ /'))"
		return 0
	fi
	REF=${REF:-main}
	SRC=$(mktemp -d)
	trap 'rm -rf "$SRC"' EXIT
	say "deploy files: github.com/$REPO at $REF"
	local f
	for f in $FILES; do
		mkdir -p "$SRC/$(dirname "$f")"
		curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/deploy/$f" -o "$SRC/$f" || die "couldn't fetch deploy/$f at $REF"
	done
}

install_files() {
	local f
	for f in compose.yml compose.server.yml Caddyfile env.example; do
		run install -m 644 -o "$WOK_USER" -g "$WOK_USER" "$SRC/$f" "$STACK/$f"
	done
	run install -m 755 -o root -g root "$SRC/wok" /usr/local/bin/wok
	printf 'WOK_HOME=%s\nWOK_USER=%s\n' "$WOK_HOME" "$WOK_USER" | write_file 644 root:root /etc/default/wok
	for f in wok-update.service wok-update.timer; do
		sed -e "s/^User=wok$/User=$WOK_USER/" -e "s/^Group=wok$/Group=$WOK_USER/" "$SRC/systemd/$f" |
			write_file 644 root:root "/etc/systemd/system/$f"
	done
}

# --- 5. the env file --------------------------------------------------------------------------------------------
ensure_env_file() {
	if [ ! -e "$ENV_FILE" ]; then
		say "creating $ENV_FILE from env.example (mode 600, owner $WOK_USER)"
		run install -m 600 -o "$WOK_USER" -g "$WOK_USER" "$SRC/env.example" "$ENV_FILE"
		if [ "$DRY_RUN" = 1 ]; then return 0; fi
		cat <<EOF

Next: fill in $ENV_FILE (sudo -e $ENV_FILE), then run this script again to log in to the registry and start.
Everything else is in place. See deploy/README.md, "Bring up a new host".
EOF
		exit 0
	fi
	run chown "$WOK_USER:$WOK_USER" "$ENV_FILE"
	run chmod 600 "$ENV_FILE"
	if [ ! -r "$ENV_FILE" ]; then
		warn "can't read $ENV_FILE as $(id -un); skipping env checks in this dry run"
		ENV_READABLE=0
		return 0
	fi
	ENV_READABLE=1
	local k missing=""
	for k in DATABASE_URL SUPABASE_URL SUPABASE_SERVICE_KEY ADMIN_URL; do
		[ -n "$(env_get "$k")" ] || missing="$missing $k"
	done
	[ -z "$missing" ] || die "$ENV_FILE has no value for:$missing"
	[ -n "$(env_get ANTHROPIC_API_KEY)" ] || warn "ANTHROPIC_API_KEY is empty: only DEMO_MODE=fixture works"
	if [ -z "$(env_get SITE_ADDRESS)" ] && [ -z "$(env_get TUNNEL_TOKEN)" ]; then
		warn "neither SITE_ADDRESS (domain) nor TUNNEL_TOKEN is set: the stack serves plain HTTP on :80"
	fi
	[ "$(env_get PUBLISH_MODE)" != graph ] || warn "PUBLISH_MODE=graph: approving a Post publishes it on Instagram"
	say "env file $ENV_FILE: required values present (not shown)"
}

check_ports() {
	local site
	site=$(env_get SITE_ADDRESS)
	case $site in '' | :* | http://*) return 0 ;; esac
	have ss || return 0
	if ss -ltnH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -q . &&
		! docker ps --format '{{.Names}}' 2>/dev/null | grep -q -- '-caddy-'; then
		warn "something else already listens on :80 or :443; Caddy needs both for $site (see 'ss -ltnp')"
	fi
}

# Build mode builds the images here from a git checkout, so it needs git (and no registry login).
build_mode() { [ "${ENV_READABLE:-0}" = 1 ] && [ "$(env_get WOK_SOURCE)" = build ]; }
ensure_git() {
	build_mode || return 0
	if have git; then
		say "build mode: git $(git --version | cut -d' ' -f3)"
	elif [ -n "$OS_ID" ]; then
		say "build mode: installing git"
		run apt-get install -y -qq git
	else
		die "WOK_SOURCE=build needs git; install it and re-run"
	fi
	local cpus mem
	cpus=$(nproc 2>/dev/null || echo 0)
	mem=$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo 2>/dev/null || echo 0)
	if [ "$cpus" -lt 4 ] || [ "$mem" -lt 7 ]; then
		warn "build mode on ${cpus} vCPU / ${mem} GB: building the images may be slow or run out of memory (4 / 8 is comfortable)"
	fi
}

# --- 6. registry login ------------------------------------------------------------------------------------------
registry_login() {
	[ "${ENV_READABLE:-0}" = 1 ] || {
		say "registry login: skipped (env file not readable in this dry run)"
		return 0
	}
	if build_mode; then
		say "registry login: skipped (build mode builds the images on this host)"
		return 0
	fi
	local image host user token
	image=$(env_get API_IMAGE)
	image=${image:-ghcr.io/juyoungml/nemo-k-cards-api}
	host=${image%%/*}
	case $host in *.*) ;; *)
		say "registry login: $image is on Docker Hub; skipped"
		return 0
		;;
	esac
	user=$(env_get GHCR_USER)
	token=$(env_get GHCR_TOKEN)
	if [ -z "$user" ] || [ -z "$token" ]; then
		warn "GHCR_USER/GHCR_TOKEN empty: pulling private images from $host will fail"
		return 0
	fi
	if [ "$DRY_RUN" = 1 ]; then
		printf '  + docker login %s -u %s --password-stdin   (as %s; token read from %s, not shown)\n' "$host" "$user" "$WOK_USER" "$ENV_FILE"
		return 0
	fi
	say "logging in to $host as $user (read-only token from the env file)"
	printf '%s' "$token" | runuser -u "$WOK_USER" -- env HOME="$WOK_HOME" DOCKER_CONFIG="$WOK_HOME/.docker" \
		docker login "$host" -u "$user" --password-stdin >/dev/null ||
		die "registry login to $host failed (GHCR needs a classic PAT with read:packages)"
}

# --- 7. updater and start ---------------------------------------------------------------------------------------
enable_updater() {
	if have systemctl && [ -d /run/systemd/system ]; then
		run systemctl daemon-reload
		run systemctl enable --now wok-update.timer
	elif [ "$DRY_RUN" = 1 ]; then
		printf '  + systemctl daemon-reload; systemctl enable --now wok-update.timer\n'
	else
		warn "no systemd: run '/usr/local/bin/wok update' every 2 minutes from cron as $WOK_USER instead"
	fi
}

start_stack() {
	if [ "$START" != 1 ]; then
		say "not starting (--no-start). Start with: sudo wok up"
		return 0
	fi
	if build_mode; then
		say "starting the stack (builds the images on the first run, ~10 minutes; waits until healthy)"
	else
		say "starting the stack (pulls the images, waits until healthy)"
	fi
	run /usr/local/bin/wok config
	run /usr/local/bin/wok up
}

main() {
	preflight
	ensure_docker
	ensure_user
	fetch_files
	install_files
	ensure_env_file
	[ "${ENV_READABLE:-0}" != 1 ] || check_ports
	ensure_git
	registry_login
	enable_updater
	start_stack
	say "done. 'sudo wok status' shows what runs; 'sudo wok pause' freezes updates. Then run the smoke checklist in deploy/README.md."
}

main
