#!/usr/bin/env zsh
# Regression harness for pullfeeds.zsh (status-feed mirror puller).
# Run from the workspace root:  zsh test_pullfeeds.zsh
#
# The harness ships its own strict `curl` stand-in (first on PATH) that
# records every argv it receives and writes deterministic payloads, so the
# suite is fully offline.
emulate -L zsh
setopt no_unset
LC_ALL=C
export LC_ALL

[[ $0 == */* ]] && cd -- "${0%/*}"

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T/bin"
TRAPEXIT() { rm -rf "$ROOT/$T"; }

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_absent() { # assert_absent <label> <path>
  (( checks += 1 ))
  if [[ -e "$2" ]]; then
    (( fails += 1 ))
    printf 'FAIL %s: %s exists but must not\n' "$1" "$2"
  fi
}

slurp() { # slurp <var> <file> -- byte-exact contents; missing file reads empty
  typeset -g "$1"=
  [[ -f "$2" ]] || return 0
  IFS= read -r -d '' "$1" < "$2" || true
}

# ---- the curl stand-in --------------------------------------------------------
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env zsh
# strict curl stand-in: records argv, understands only the flags the team uses
emulate -L zsh
setopt no_unset
typeset -a got
got=("$@")
{
  typeset a
  for a in "${got[@]}"; do
    print -r -- "$a"
  done
  print -r -- "--"
} >> "$CURL_CALLS"

outfile=''
url=''
while (( $# > 0 )); do
  case $1 in
    -s) ;;
    --max-time|--retry) shift ;;
    -o) shift; outfile=$1 ;;
    -*) print -ru2 -- "curl-stub: unknown option: $1"; exit 2 ;;
    *) url=$1 ;;
  esac
  shift
done
if [[ -z $url ]]; then
  print -ru2 -- "curl-stub: no url given"
  exit 2
fi
[[ $url == *unreachable* ]] && exit 7
if [[ -n $outfile ]]; then
  print -r -- "payload for $url" > "$outfile"
fi
exit 0
STUB
chmod +x "$T/bin/curl"

typeset RC OUT ERR
run_pull() { # run_pull <calls-log> <args...>
  local log=$1
  shift
  : > "$ROOT/$T/$log"
  ( cd "$ROOT/$T" \
      && CURL_CALLS="$ROOT/$T/$log" PATH="$ROOT/$T/bin:$PATH" \
         exec zsh "$ROOT/pullfeeds.zsh" "$@" ) \
    > "$ROOT/$T/.out" 2> "$ROOT/$T/.err"
  RC=$?
  slurp OUT "$ROOT/$T/.out"
  slurp ERR "$ROOT/$T/.err"
}

if [[ ! -f pullfeeds.zsh ]]; then
  printf 'FAIL pullfeeds.zsh not found in the workspace root\n'
  exit 1
fi

nl=$'\n'

# ---- 1. a normal mirror run ----------------------------------------------------
{
  printf 'status\thttps://feeds.example.test/status.xml?fmt=rss\t\n'
  printf 'builds\thttps://feeds.example.test/builds.xml?fmt=rss&days=2\t--retry 2\n'
  printf 'archive\thttps://feeds.example.test/archive.xml\t\n'
  printf 'legacy\thttps://feeds.example.test/legacy.xml\t\n'
  printf 'notices\thttps://feeds.example.test/notices.xml\t\n'
} > "$T/feeds.tsv"

run_pull calls1.log feeds.tsv 'mirror out'

assert_eq 'mirror run: exit code' '0' "$RC"
assert_eq 'mirror run: stderr is quiet' '' "$ERR"
expected="gateway ok${nl}"
expected+="pulled status${nl}"
expected+="pulled builds${nl}"
expected+="skipped archive${nl}"
expected+="skipped legacy${nl}"
expected+="pulled notices${nl}"
expected+="done: 3 pulled, 0 failed${nl}"
assert_eq 'mirror run: stdout' "$expected" "$OUT"

PING=''
slurp PING "$T/mirror out/ping.txt"
assert_eq 'mirror run: gateway ping fetched' \
  "payload for https://gw.example.test/ping?src=mirror${nl}" "$PING"

STATUS=''
slurp STATUS "$T/mirror out/status.xml"
assert_eq 'mirror run: status.xml payload (url arrived intact)' \
  "payload for https://feeds.example.test/status.xml?fmt=rss${nl}" "$STATUS"

BUILDS=''
slurp BUILDS "$T/mirror out/builds.xml"
assert_eq 'mirror run: builds.xml payload (query string intact)' \
  "payload for https://feeds.example.test/builds.xml?fmt=rss&days=2${nl}" "$BUILDS"

NOTICES=''
slurp NOTICES "$T/mirror out/notices.xml"
assert_eq 'mirror run: notices.xml payload' \
  "payload for https://feeds.example.test/notices.xml${nl}" "$NOTICES"

assert_absent 'mirror run: archive is excluded' "$T/mirror out/archive.xml"
assert_absent 'mirror run: legacy is excluded'  "$T/mirror out/legacy.xml"

# every option must reach curl as its own argument, urls as one argument each
expected="-s${nl}--max-time${nl}5${nl}-o${nl}mirror out/ping.txt${nl}https://gw.example.test/ping?src=mirror${nl}--${nl}"
expected+="-s${nl}--max-time${nl}5${nl}-o${nl}mirror out/status.xml${nl}https://feeds.example.test/status.xml?fmt=rss${nl}--${nl}"
expected+="-s${nl}--max-time${nl}5${nl}--retry${nl}2${nl}-o${nl}mirror out/builds.xml${nl}https://feeds.example.test/builds.xml?fmt=rss&days=2${nl}--${nl}"
expected+="-s${nl}--max-time${nl}5${nl}-o${nl}mirror out/notices.xml${nl}https://feeds.example.test/notices.xml${nl}--${nl}"
CALLS=''
slurp CALLS "$T/calls1.log"
assert_eq 'mirror run: exact argv of every curl call' "$expected" "$CALLS"

# ---- 2. a feed that is down is reported and counted ----------------------------
{
  printf 'status\thttps://feeds.example.test/status.xml?fmt=rss\t\n'
  printf 'mainframe\thttps://unreachable.example.test/mf.xml\t\n'
} > "$T/down.tsv"

run_pull calls2.log down.tsv out2

assert_eq 'down feed: exit code' '1' "$RC"
assert_eq 'down feed: stderr' "pull failed: mainframe${nl}" "$ERR"
expected="gateway ok${nl}"
expected+="pulled status${nl}"
expected+="done: 1 pulled, 1 failed${nl}"
assert_eq 'down feed: stdout' "$expected" "$OUT"
assert_absent 'down feed: no partial file' "$T/out2/mainframe.xml"

# ---- 3. argument validation -----------------------------------------------------
run_pull calls3.log nope.tsv out3
assert_eq 'missing conf: exit code' '2' "$RC"
assert_eq 'missing conf: stderr' "pullfeeds: no such config: nope.tsv${nl}" "$ERR"

run_pull calls4.log
assert_eq 'usage: exit code' '2' "$RC"
assert_eq 'usage: stderr' "usage: pullfeeds.zsh <feeds.conf> <out-dir>${nl}" "$ERR"

# ---- summary ----------------------------------------------------------------------
if (( fails > 0 )); then
  printf '%d/%d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'ok - %d checks passed\n' "$checks"
