#!/usr/bin/env bash
# Read a published chart as a stranger would: with no registry login at all.
#
#   bash .github/scripts/read-chart-anonymously.sh <oci-repository>/<chart> <version> [--plain-http]
#
#   bash .github/scripts/read-chart-anonymously.sh oci://ghcr.io/thonicdev/charts/kubetower 0.3.0
#
# release.yml runs it after the login is gone. The push and the read-back in
# publish-chart.sh are authenticated, so they pass for a package only its
# owners can read - and on ghcr.io a new package can start private. Then every
# check is green and `helm install oci://...` fails for everybody else. This is
# the check that sees it.
#
# Helm reads its registry credentials from HELM_REGISTRY_CONFIG, and from the
# Docker credential store when that file names none. Both are pointed at
# empty places here, so whatever login the runner holds is not used.
set -euo pipefail

[ $# -ge 2 ] || { echo "usage: $0 <oci-repository>/<chart> <version> [--plain-http]" >&2; exit 2; }
ref=$1 version=$2 plain=()
[ "${3:-}" = --plain-http ] && plain=(--plain-http)

empty=$(mktemp -d)
trap 'rm -rf "$empty"' EXIT
printf '{}\n' > "$empty/config.json"
if ! out=$(HELM_REGISTRY_CONFIG="$empty/config.json" DOCKER_CONFIG="$empty" \
           helm show chart "$ref" --version "$version" "${plain[@]}" 2>&1); then
  echo "NOT READABLE: $ref $version was pushed, and cannot be read without a login. On ghcr.io a new package starts private: make the package public in its settings, then re-run this job. Until then the README's install command fails for everybody:" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  exit 1
fi
got=$(printf '%s\n' "$out" | sed -n 's/^version:[[:space:]]*//p' | tr -d '"')
[ "$got" = "$version" ] || { echo "read $ref anonymously, and it says version $got, want $version" >&2; exit 1; }
echo "read $ref $version with no login: readable by anybody"
