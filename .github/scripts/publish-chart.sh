#!/usr/bin/env bash
# Package the chart, push it to an OCI registry, and read it back.
#
#   bash .github/scripts/publish-chart.sh <tag> <oci-repository> [options]
#
#     --plain-http          talk to the registry without TLS (CI's own registry)
#     --allow-placeholder   publish while appVersion is the placeholder (CI's dry run only)
#     --on-branch <ref>     refuse unless the commit being published is on <ref>
#
#   bash .github/scripts/publish-chart.sh kubetower-0.3.0 oci://ghcr.io/thonicdev/charts --on-branch origin/main
#
# release.yml runs it on a chart tag, against ghcr.io. chart.yml runs the same
# file on every pull request against a throwaway registry on the runner, with
# --plain-http, so the steps a release takes are the steps CI has run - not a
# copy of them that drifts.
#
# It refuses, before anything is pushed:
#
#   - a tag that is not `kubetower-<version>` for the version in Chart.yaml. A
#     published version cannot be taken back, so a typo in a tag must stop here;
#   - with --on-branch, a commit that is not on that branch: a tag pushed on a
#     side branch would otherwise publish code nobody merged;
#   - the placeholder appVersion. While appVersion is 0.0.0-dev the chart
#     refuses to render without image.tag, so a published copy would install
#     nothing by default. --allow-placeholder lifts this for CI's dry run only;
#   - a role derived from another console than the one it installs. The role
#     grants exactly what console-needs.yaml lists, and the list was read from
#     the console at `derivedFrom.commit`. The console's tag v<appVersion> must
#     be that commit, read from the console's repository - so a console that
#     asks for something new cannot ship with a chart whose role refuses it.
#     A tag that cannot be read is refused too: the tie is the point;
#   - a version the registry already holds. An OCI tag can be moved, so a
#     re-run, or a tag deleted and pushed again elsewhere, would silently
#     replace what `--version` resolves to. Only an explicit "not found" from
#     the registry counts as absent; any other answer stops the run.
#
# After the push it pulls the chart back, compares it byte for byte with the
# package it pushed, and renders the pulled copy. Whether a stranger can read
# it is .github/scripts/read-chart-anonymously.sh's question, asked after the
# login is gone.
#
# CHART_DIR (default charts/kubetower) and CONSOLE_REPO (default the console's
# GitHub repository) exist so that CI can prove the refusals on a copy.
#
# The registry login is the caller's: this script holds no credential.
set -euo pipefail

usage() { echo "usage: $0 <tag> <oci-repository> [--plain-http] [--allow-placeholder] [--on-branch <ref>]" >&2; exit 2; }
[ $# -ge 2 ] || usage
tag=$1 repo=${2%/}; shift 2
plain=() allow_placeholder=false on_branch=
while [ $# -gt 0 ]; do
  case $1 in
    --plain-http) plain=(--plain-http) ;;
    --allow-placeholder) allow_placeholder=true ;;
    --on-branch) [ $# -ge 2 ] || usage; on_branch=$2; shift ;;
    *) usage ;;
  esac
  shift
done
case $repo in oci://*) ;; *) echo "the repository must be an oci:// reference, not $repo" >&2; exit 2 ;; esac

chart=${CHART_DIR:-charts/kubetower}
console=${CONSOLE_REPO:-https://github.com/thonicdev/kubetower}
field() { sed -n "s/^$1:[[:space:]]*//p" "$chart/Chart.yaml" | tr -d '"' | head -n1; }
name=$(field name)
version=$(field version)
app=$(field appVersion)
[ -n "$name" ] && [ -n "$version" ] || { echo "could not read the chart's name and version from $chart/Chart.yaml" >&2; exit 2; }

if [ "$tag" != "$name-$version" ]; then
  echo "REFUSED: the tag is $tag, and Chart.yaml says $name $version - tag $name-$version, or change the version first" >&2
  exit 1
fi

if [ -n "$on_branch" ]; then
  head=$(git rev-parse HEAD)
  if ! git merge-base --is-ancestor "$head" "$on_branch"; then
    echo "REFUSED: $head is not on $on_branch - a chart is published only from a commit that was merged" >&2
    exit 1
  fi
fi

if [ "$app" = 0.0.0-dev ]; then
  if [ "$allow_placeholder" != true ]; then
    echo "REFUSED: appVersion is the 0.0.0-dev placeholder, which names no image; the published chart would refuse to render without image.tag. Set appVersion to a released console version first" >&2
    exit 1
  fi
else
  derived=$(sed -n 's/^[[:space:]]*commit:[[:space:]]*//p' "$chart/console-needs.yaml" | head -n1)
  [ -n "$derived" ] || { echo "REFUSED: $chart/console-needs.yaml names no derivedFrom commit" >&2; exit 1; }
  if ! refs=$(GIT_TERMINAL_PROMPT=0 timeout 60 git ls-remote "$console" "refs/tags/v$app" "refs/tags/v$app^{}" 2>&1); then
    echo "REFUSED: the console's tag v$app could not be read from $console, so the role cannot be tied to the console this chart installs:" >&2
    printf '%s\n' "$refs" | sed 's/^/    /' >&2
    exit 1
  fi
  # An annotated tag's own object, then the commit it points at (^{}): the commit wins.
  tagged=$(printf '%s\n' "$refs" | awk -v p="refs/tags/v$app^{}" '$2 == p {print $1}')
  [ -n "$tagged" ] || tagged=$(printf '%s\n' "$refs" | awk -v p="refs/tags/v$app" '$2 == p {print $1}')
  [ -n "$tagged" ] || { echo "REFUSED: the console has no tag v$app at $console - appVersion must name a released console" >&2; exit 1; }
  if [ "$tagged" != "$derived" ]; then
    echo "REFUSED: the console's v$app is $tagged, and console-needs.yaml was derived from $derived. Re-run test/derive-needs.py at $tagged, move derivedFrom to it, then tag again" >&2
    exit 1
  fi
fi

if out=$(helm show chart "$repo/$name" --version "$version" "${plain[@]}" 2>&1); then
  echo "REFUSED: $repo/$name $version is already published, and a published version is never replaced. Bump the chart's version" >&2
  exit 1
fi
if ! printf '%s\n' "$out" | grep -qiE 'not found|manifest unknown|name unknown|MANIFEST_UNKNOWN|NAME_UNKNOWN'; then
  echo "could not tell whether $repo/$name $version is already published, so nothing was pushed:" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
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

# Read it back. helm pull writes into the destination and does not create it.
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
