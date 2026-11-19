#!/usr/bin/env bash
# Refuse a reference that a reader of this public repository cannot follow.
#
#   bash test/public-check.sh tree              every tracked file, this one
#                                               included - its name and its content
#   bash test/public-check.sh commits <range>   every commit message in a git range
#   bash test/public-check.sh message <label>   the text on stdin - a pull
#                                               request's title and description
#   bash test/public-check.sh --self-test       prove every scan can fail, then stop
#
# What it refuses: identifiers from the project's private decision and fix
# registers - a D, O or FX, a hyphen and a number, in any case. A reference
# nobody outside can open is worse than none, because it reads like evidence.
# It holds no list of names: a list of what must not be published would
# publish it.
#
# One allowance, in messages only: a line that does nothing but close an issue
# in another repository - `Closes <repository>#58`, optionally with the
# organisation in front. That is how the loop back to an issue tracked
# elsewhere closes. A file in the tree gets no allowance, and neither does a
# Closes line carrying anything else.
#
# Why messages as well as the tree: this repository squash-merges with the pull
# request's title and description as the commit message, so the description is
# published on `main` exactly as a file is. The commit messages of the branch
# do not reach `main`, but they stay readable on the pull request for good.
#
# Exit status: 0 clean, 1 a reference was found, 2 the scan itself failed. The
# last is not a pass: a scan that could not read is a scan that saw nothing.
set -u

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

ids='\b(D|O|FX)-[0-9]+\b'

# The allowed Closes line, lower case because it is compared lower-cased.
closes='^[[:space:]]*closes[[:space:]]+([a-z0-9_.-]+/)?[a-z0-9_.-]+#[0-9]+[[:space:]]*$'

# Blank every line that only closes an issue elsewhere, keeping the line
# numbers of the rest.
allow_closes() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ ${line,,} =~ $closes ]]; then echo; else printf '%s\n' "$line"; fi
  done
}

# Print every matching line of stdin, prefixed with $1. With $2 = closes, a
# line that only closes a documentation issue is not read. Returns what grep
# returns: 0 when there was a match, 1 when there was none, 2 when it failed.
match() {
  local label=$1 allow=${2:-} input found=1 rc
  input=$(cat) || return 2
  [ "$allow" = closes ] && input=$(printf '%s\n' "$input" | allow_closes)
  printf '%s\n' "$input" | grep -niE -e "$ids" | sed "s|^|$label:|"
  rc=${PIPESTATUS[1]}
  case $rc in
    0) found=0 ;;
    1) ;;
    *) echo "grep failed ($rc) on $label" >&2; return 2 ;;
  esac
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
    # The path first: a file can publish a name by being called it.
    printf '%s\n' "$f" | match "file name $f"
    rc=$?
    [ $rc -eq 2 ] && return 2
    [ $rc -eq 0 ] && hit=1
    [ -f "$f" ] || continue
    match "$f" < "$f"
    rc=$?
    [ $rc -eq 2 ] && return 2
    [ $rc -eq 0 ] && hit=1
  done
  echo "scanned ${#files[@]} tracked files, their names and their content" >&2
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
    git log -1 --format=%B "$c" | match "commit $(git rev-parse --short "$c")" closes
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
  # A title is never empty, so an empty message is a message that did not
  # arrive - an unset variable, a wrong event field - not a clean one.
  if [ -z "${text//[[:space:]]/}" ]; then
    echo "the $label is empty - nothing was scanned" >&2
    return 2
  fi
  printf '%s\n' "$text" | match "$label" closes
  rc=${PIPESTATUS[1]}
  [ $rc -eq 2 ] && return 2
  echo "scanned the $label, $(printf '%s\n' "$text" | wc -l | tr -d ' ') lines" >&2
  # match answers "found", so its 0 is this scan's 1.
  [ $rc -eq 1 ] && return 0
  return 1
}

self_test() {
  local tmp rc n=0 planted
  local docs='example-docs' id35="D-$((30 + 5))"
  # One of each identifier, in capitals and in lower case.
  for id in D O FX d o fx; do
    printf 'decided in %s-%d\n' "$id" 35 | match planted > /dev/null
    [ $? -eq 0 ] || { echo "self-test: an $id identifier was not caught" >&2; return 2; }
    n=$((n + 1))
  done
  # And the things that look close and are ordinary.
  local ordinary=('sha-256' 'ISO-8601' 'a D-day' 'kubetower-helm-charts#2' 'FX rates'
    'covid-19' 'x-1' 'demo-1' 'utf-8' 'the KubeTower console' 'Closes #5')
  printf '%s\n' "${ordinary[@]}" | match clean > /dev/null
  [ $? -eq 1 ] || { echo "self-test: an ordinary line was refused" >&2; return 2; }

  # The Closes allowance: a line that only closes an issue elsewhere passes
  # a message, and nothing else rides on it.
  printf 'feat: a change\n\nCloses thonicdev/%s#58\r\ncloses %s#59\n' "$docs" "$docs" \
    | bash "$self" message "planted description" 2> /dev/null > /dev/null
  [ $? -eq 0 ] || { echo "self-test: a Closes line was refused" >&2; return 2; }
  for planted in "see $id35" "Closes $docs#58 as decided in $id35" \
                 "Closes $docs#58, and $id35 has the rest" "Fixes $docs#58 ($id35)"; do
    printf 'feat: a change\n\n%s\n' "$planted" | bash "$self" message "planted description" > /dev/null 2>&1
    [ $? -eq 1 ] || { echo "self-test: '$planted' passed a message" >&2; return 2; }
    n=$((n + 1))
  done
  printf '' | bash "$self" message "planted description" > /dev/null 2>&1
  [ $? -eq 2 ] || { echo "self-test: an empty message read as clean" >&2; return 2; }

  # Then the scans end to end, in a throwaway repository.
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
  # A squash of a pull request closing a documentation issue lands on main.
  ( cd "$tmp" && git commit -q --allow-empty -m "feat: a change" -m "Closes thonicdev/$docs#58" ) \
    || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~1..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 0 ] || { echo "self-test: a squash closing an issue was refused ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" \
    && git commit -q --allow-empty -m "feat: a change" -m "as decided in d-$((30 + 5))" ) \
    || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" commits HEAD~3..HEAD ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted commit message passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  # A file named after a register entry, with nothing wrong inside it.
  ( cd "$tmp" && printf 'clean\n' > "${id35}-notes.md" && git add "${id35}-notes.md" ) \
    || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" tree ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted file name passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && git rm -q --cached "${id35}-notes.md" && rm "${id35}-notes.md" ) \
    || { rm -rf "$tmp"; return 2; }
  # And a file citing a register entry.
  ( cd "$tmp" && printf 'as decided in %s\n' "$id35" > b.txt && git add b.txt ) || { rm -rf "$tmp"; return 2; }
  ( cd "$tmp" && bash "$self" tree ) > /dev/null 2>&1
  rc=$?
  [ $rc -eq 1 ] || { echo "self-test: a planted file passed ($rc)" >&2; rm -rf "$tmp"; return 2; }
  rm -rf "$tmp"
  echo "ok    self-test - $n planted references caught, ${#ordinary[@]} ordinary lines and two"
  echo "      Closes lines passed, and a planted commit message, file name and file"
  echo "      each failed a real scan while a squash closing an issue did not"
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  tree) tree; rc=$? ;;
  commits)
    [ -n "${2:-}" ] || { echo "usage: $0 commits <range>" >&2; exit 2; }
    commits "$2"; rc=$? ;;
  message)
    [ -n "${2:-}" ] || { echo "usage: $0 message <label> < text" >&2; exit 2; }
    message "$2"; rc=$? ;;
  *) echo "usage: $0 tree | commits <range> | message <label> | --self-test" >&2; exit 2 ;;
esac

case $rc in
  0) echo "no private references" ;;
  1) echo "::error::a private reference would be published - state the reasoning in plain words instead" ;;
  *) echo "::error::the scan could not run, so it saw nothing" ;;
esac
exit $rc
