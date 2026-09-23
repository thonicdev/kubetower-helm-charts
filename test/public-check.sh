#!/usr/bin/env bash
# Refuse a reference that a reader of this public repository cannot follow.
#
#   bash test/public-check.sh tree              every tracked file, this one included
#   bash test/public-check.sh commits <range>   every commit message in a git range
#   bash test/public-check.sh --self-test       prove both scans can fail, then stop
#
# What it refuses: the names of the organisation's private repositories and
# documents, and identifiers from its private decision and fix registers - a
# capital D, O or FX, a hyphen and a number. A reference nobody outside can
# open is worse than none, because it reads like evidence.
#
# Why commit messages as well as the tree: a squash merge can carry every
# commit message of a pull request into the body of the one commit that lands
# on `main`, so a message is published exactly as a file is.
#
# The names are assembled from pieces below, so that this file does not spell
# what it refuses - the tree scan reads it like any other file and excludes
# nothing. A pattern the operator does not want to publish at all can be added
# through the PUBLIC_CHECK_EXTRA environment variable, which the workflow fills
# from a repository variable of the same name when one exists.
#
# Exit status: 0 clean, 1 a reference was found, 2 the scan itself failed. The
# last is not a pass: a scan that could not read is a scan that saw nothing.
set -u

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

names="kubetower"'-docs|claude'"-work|claude"'-plugins|claude'"-global|server-mode"'-threat-model'
ids='\b(D|O|FX)-[0-9]+\b'
extra="${PUBLIC_CHECK_EXTRA:-}"

# Print every matching line of stdin, prefixed with $1. Returns what grep
# returns: 0 when there was a match, 1 when there was none, 2 when it failed.
match() {
  local label=$1 input found=1 rc
  input=$(cat) || return 2
  for spec in "i:$names" "s:$ids" ${extra:+"s:$extra"}; do
    local flags=-nE
    [ "${spec%%:*}" = i ] && flags=-niE
    printf '%s\n' "$input" | grep $flags -e "${spec#*:}" | sed "s|^|$label:|"
    rc=${PIPESTATUS[1]}
    case $rc in
      0) found=0 ;;
      1) ;;
      *) echo "grep failed ($rc) on $label" >&2; return 2 ;;
    esac
  done
  return $found
}

tree() {
  local files hit=0 rc
  mapfile -d '' files < <(git ls-files -z) || return 2
  if [ "${#files[@]}" -eq 0 ]; then
    echo "git ls-files returned no files - nothing was scanned" >&2
    return 2
  fi
  for f in "${files[@]}"; do
    [ -f "$f" ] || continue
    match "$f" < "$f"
    rc=$?
    [ $rc -eq 2 ] && return 2
    [ $rc -eq 0 ] && hit=1
  done
  echo "scanned ${#files[@]} tracked files" >&2
  return $hit
}

commits() {
  local range=$1 shas hit=0 rc
  shas=$(git rev-list "$range") || { echo "git rev-list $range failed" >&2; return 2; }
  if [ -z "$shas" ]; then
    # Empty is not clean: a range with nothing in it is usually a range that
    # was computed against a history the checkout does not have.
    echo "no commits in $range - nothing was scanned" >&2
    return 2
  fi
  for c in $shas; do
    git log -1 --format=%B "$c" > /dev/null || return 2
    git log -1 --format=%B "$c" | match "commit $(git rev-parse --short "$c")"
    rc=${PIPESTATUS[1]}
    [ $rc -eq 2 ] && return 2
    [ $rc -eq 0 ] && hit=1
  done
  echo "scanned $(printf '%s\n' "$shas" | wc -l | tr -d ' ') commit messages in $range" >&2
  return $hit
}

self_test() {
  local tmp rc n=0
  # Every refused name, and one of each identifier, built at run time.
  local IFS='|'
  for name in $names; do
    printf 'see %s for why\n' "$name" | match planted > /dev/null
    [ $? -eq 0 ] || { echo "self-test: '$name' was not caught" >&2; return 2; }
    n=$((n + 1))
  done
  unset IFS
  for id in D O FX; do
    printf 'decided in %s-%d\n' "$id" 35 | match planted > /dev/null
    [ $? -eq 0 ] || { echo "self-test: an $id identifier was not caught" >&2; return 2; }
    n=$((n + 1))
  done
  # And the things that look close and are ordinary.
  printf '%s\n' 'sha-256' 'ISO-8601' 'a D-day' 'kubetower-helm-charts#2' 'FX rates' \
    | match clean > /dev/null
  [ $? -eq 1 ] || { echo "self-test: an ordinary line was refused" >&2; return 2; }

  # Then the two scans end to end, in a throwaway repository.
  tmp=$(mktemp -d) || return 2
  (
    set -e
    cd "$tmp"
    git init -q
    git config user.name self-test
    git config user.email self-test@example.invalid
    git config core.autocrlf false
    printf 'clean\n' > a.txt
    git add a.txt
    git commit -q -m 'chore: a clean start'
    git commit -q --allow-empty -m 'chore: a clean message'
  ) || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" tree ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 0 ] || { echo "self-test: a clean tree was refused ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~1..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 0 ] || { echo "self-test: a clean message was refused ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" \
    && git commit -q --allow-empty -m "feat: a change" -m "as decided in D-$((30 + 5))" ) \
    || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~2..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted commit message passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && printf 'see %s\n' "${names%%|*}" > b.txt && git add b.txt ) || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" tree ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted file passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  rm -rf "$tmp"
  echo "ok    self-test - $n planted references caught, 5 ordinary lines passed,"
  echo "      and a planted commit message and a planted file both failed a real scan"
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  tree) tree; rc=$? ;;
  commits)
    [ -n "${2:-}" ] || { echo "usage: $0 commits <range>" >&2; exit 2; }
    commits "$2"; rc=$? ;;
  *) echo "usage: $0 tree | commits <range> | --self-test" >&2; exit 2 ;;
esac

case $rc in
  0) echo "no private references" ;;
  1) echo "::error::a private reference would be published - state the reasoning in plain words instead" ;;
  *) echo "::error::the scan could not run, so it saw nothing" ;;
esac
exit $rc
