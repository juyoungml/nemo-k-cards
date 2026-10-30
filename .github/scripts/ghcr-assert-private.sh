#!/usr/bin/env bash
# Fails if a GHCR package can be pulled anonymously, i.e. it is PUBLIC.
#   .github/scripts/ghcr-assert-private.sh <owner>/<package>
# The repo is public but its images are private (deploy/README.md). GHCR hands out an anonymous pull token
# only for public packages (HTTP 200); a private or missing package answers 401/403. New packages start
# private, and making one public cannot be undone, so this guards against someone flipping it by mistake.
# No credentials are used or printed.
set -euo pipefail
image=$(printf '%s' "${1:?usage: $0 <owner>/<package>}" | tr '[:upper:]' '[:lower:]')
code=$(curl -s -o /dev/null -w '%{http_code}' "https://ghcr.io/token?scope=repository:${image}:pull")
case "$code" in
  200)
    echo "::error::ghcr.io/${image} is PUBLIC: anyone can pull it. Set it to private in the package settings (deploy/README.md)."
    exit 1 ;;
  401|403)
    echo "ghcr.io/${image}: private (anonymous pull refused, HTTP ${code})" ;;
  *)
    echo "::error::could not check ghcr.io/${image} visibility (HTTP ${code})"
    exit 1 ;;
esac
