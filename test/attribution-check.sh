#!/usr/bin/env bash
# Refuse machine attribution wherever it would enter this repository's record.
#
#   bash test/attribution-check.sh commits <range>   every commit message in a git range
#   bash test/attribution-check.sh message <label>   the text on stdin - a pull
#                                                    request's title and description
#   bash test/attribution-check.sh --self-test       prove both scans can fail, then stop
#
# What it refuses: a co-author trailer naming a model or its vendor, and a
# "generated with" line naming one. This project is dual-licensed, which is
# lawful only while the copyright holder is the sole author, so the history is
# the record that argument is made from.
#
# Why the description as well as the commits: this repository squash-merges
# with the pull request's title and description as the commit message, so the
# description is the one text that certainly lands on `main` - and it is the
# place a tool writes its "generated with" line.
#
# Exit status: 0 clean, 1 attribution found, 2 the scan itself failed. The last
# is not a pass: a scan that could not read is a scan that saw nothing.
set -u

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
pattern='co-authored-by:.*(claude|anthropic)|generated with .*claude'

# Print every matching line of stdin, prefixed with $1. 0 found, 1 none, 2 failed.
match() {
  local label=$1 rc
  grep -niE -e "$pattern" | sed "s|^|$label:|"
  rc=${PIPESTATUS[0]}
  [ $rc -le 1 ] || { echo "grep failed ($rc) on $label" >&2; return 2; }
  return $rc
}

commits() {
  local range=$1 shas hit=0 rc c
  shas=$(git rev-list "$range") || { echo "git rev-list $range failed" >&2; return 2; }
  if [ -z "$shas" ]; then
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

message() {
  local label=$1 text rc
  text=$(cat) || return 2
  # A title is never empty, so an empty message did not arrive.
  if [ -z "${text//[[:space:]]/}" ]; then
    echo "the $label is empty - nothing was scanned" >&2
    return 2
  fi
  printf '%s\n' "$text" | match "$label"
  rc=${PIPESTATUS[1]}
  [ $rc -eq 2 ] && return 2
  echo "scanned the $label, $(printf '%s\n' "$text" | wc -l | tr -d ' ') lines" >&2
  [ $rc -eq 1 ] && return 0
  return 1
}

self_test() {
  local tmp rc n=0 planted
  # Built from pieces, so that a search of the tree for attribution does not
  # land on its own test.
  local model="Clau""de" vendor="anth""ropic"
  for planted in "Co-Authored-By: $model <noreply@$vendor.com>" \
                 "co-authored-by: A Model <noreply@$vendor.com>" \
                 "Generated with [$model Code](https://example.invalid)" \
                 "GENERATED WITH ${model^^}"; do
    printf 'feat: a change\n\n%s\n' "$planted" | bash "$self" message "planted description" > /dev/null 2>&1
    [ $? -eq 1 ] || { echo "self-test: '$planted' passed a message" >&2; return 2; }
    n=$((n + 1))
  done
  printf 'feat: a change\n\nCo-Authored-By: A Person <a.person@example.com>\ngenerated with helm template\nCloses #5\n' \
    | bash "$self" message "clean description" > /dev/null 2>&1
  [ $? -eq 0 ] || { echo "self-test: an ordinary description was refused" >&2; return 2; }
  printf '' | bash "$self" message "empty description" > /dev/null 2>&1
  [ $? -eq 2 ] || { echo "self-test: an empty description read as clean" >&2; return 2; }

  tmp=$(mktemp -d) || return 2
  (
    set -e
    cd "$tmp"
    git init -q
    git config user.name self-test
    git config user.email self-test@example.invalid
    git commit -q --allow-empty -m 'chore: a clean start'
    git commit -q --allow-empty -m 'chore: a clean message'
  ) || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~1..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 0 ] || { echo "self-test: a clean message was refused ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && git commit -q --allow-empty -m "feat: a change" -m "Co-Authored-By: $model <noreply@$vendor.com>" ) \
    || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~2..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted commit message passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  rm -rf "$tmp"
  echo "ok    self-test - $n planted attributions refused in a description, an ordinary"
  echo "      one passed, an empty one was not read as clean, and a planted commit failed"
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  commits)
    [ -n "${2:-}" ] || { echo "usage: $0 commits <range>" >&2; exit 2; }
    commits "$2"; rc=$? ;;
  message)
    [ -n "${2:-}" ] || { echo "usage: $0 message <label> < text" >&2; exit 2; }
    message "$2"; rc=$? ;;
  *) echo "usage: $0 commits <range> | message <label> | --self-test" >&2; exit 2 ;;
esac

case $rc in
  0) echo "no machine attribution" ;;
  1) echo "::error::the text above names a machine as co-author or generator - remove the line" ;;
  *) echo "::error::the scan could not run, so it saw nothing" ;;
esac
exit $rc
