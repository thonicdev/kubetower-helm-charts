#!/usr/bin/env bash
# Package the chart, push it to an OCI registry, and read it back.
#
#   bash .github/scripts/publish-chart.sh <tag> <oci-repository> [--plain-http] [--allow-placeholder]
#
#   bash .github/scripts/publish-chart.sh kubetower-0.3.0 oci://ghcr.io/thonicdev/charts
#
# release.yml runs it on a chart tag, against ghcr.io. chart.yml runs the same
# file on every pull request against a throwaway registry on the runner, with
# --plain-http, so the steps a release takes are the steps CI has run - not a
# copy of them that drifts.
#
# It refuses, before anything leaves the runner:
#
#   - a tag that is not `kubetower-<version>` for the version in Chart.yaml. A
#     published version cannot be taken back, so a typo in a tag must stop here;
#   - the placeholder appVersion. While appVersion is 0.0.0-dev the chart
#     refuses to render without image.tag, so a published copy would install
#     nothing by default. --allow-placeholder lifts this for CI's dry run only.
#
# After the push it pulls the chart back, compares it byte for byte with the
# package it pushed, and renders the pulled copy - so "published" means a copy
# somebody else can fetch and install, read from the registry, not the exit
# status of a push.
#
# The registry login is the caller's: this script holds no credential.
set -euo pipefail

usage() { echo "usage: $0 <tag> <oci-repository> [--plain-http] [--allow-placeholder]" >&2; exit 2; }
[ $# -ge 2 ] || usage
tag=$1 repo=${2%/}; shift 2
plain=() allow_placeholder=false
for arg in "$@"; do
  case $arg in
    --plain-http) plain=(--plain-http) ;;
    --allow-placeholder) allow_placeholder=true ;;
    *) usage ;;
  esac
done
case $repo in oci://*) ;; *) echo "the repository must be an oci:// reference, not $repo" >&2; exit 2 ;; esac

chart=charts/kubetower
field() { helm show chart "$chart" | sed -n "s/^$1:[[:space:]]*//p" | tr -d '"' | head -n1; }
name=$(field name)
version=$(field version)
app=$(field appVersion)
[ -n "$name" ] && [ -n "$version" ] || { echo "could not read the chart's name and version from $chart/Chart.yaml" >&2; exit 2; }

if [ "$tag" != "$name-$version" ]; then
  echo "REFUSED: the tag is $tag, and Chart.yaml says $name $version - tag $name-$version, or change the version first" >&2
  exit 1
fi
if [ "$app" = 0.0.0-dev ] && [ "$allow_placeholder" != true ]; then
  echo "REFUSED: appVersion is the 0.0.0-dev placeholder, which names no image; the published chart would refuse to render without image.tag. Set appVersion to a released console version first" >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
helm package "$chart" --destination "$work/out"
pkg="$work/out/$name-$version.tgz"
[ -f "$pkg" ] || { echo "helm package did not write $pkg" >&2; exit 1; }

helm push "$pkg" "$repo" "${plain[@]}" 2>&1 | tee "$work/push.log"
digest=$(sed -n 's/^Digest:[[:space:]]*//p' "$work/push.log")
[ -n "$digest" ] || { echo "the push printed no digest - nothing says the registry holds the chart" >&2; exit 1; }

# Read it back, as somebody installing it would. helm pull writes into the
# destination and does not create it.
mkdir -p "$work/back"
helm pull "$repo/$name" --version "$version" --destination "$work/back" "${plain[@]}"
back="$work/back/$name-$version.tgz"
[ -f "$back" ] || { echo "the registry did not return $name $version" >&2; exit 1; }
if ! cmp -s "$pkg" "$back"; then
  echo "the chart pulled back from $repo differs from the package pushed" >&2
  exit 1
fi
got=$(helm show chart "$back" | sed -n 's/^version:[[:space:]]*//p' | tr -d '"')
[ "$got" = "$version" ] || { echo "the pulled chart says version $got, want $version" >&2; exit 1; }

# And it installs: the pulled copy renders, and its role is the one rendered
# from console-needs.yaml, which therefore shipped inside the package.
render=(--set image.tag=0.0.0-publish-check)
[ "$app" = 0.0.0-dev ] || render=()
helm template "$name" "$back" "${render[@]}" --show-only templates/rbac.yaml > "$work/role.yaml"
rules=$(grep -c 'apiGroups:' "$work/role.yaml" || true)
[ "$rules" -gt 0 ] || { echo "the pulled chart renders a ClusterRole with no rules" >&2; exit 1; }

echo "published $name $version to $repo/$name, digest $digest; pulled back identical, renders a ClusterRole of $rules rules"
