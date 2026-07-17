#!/usr/bin/env zsh
# Acceptance harness for portwrap.zsh.
# Run from the workspace root:  zsh test_portwrap.zsh
#
# The GNU userland on this box exercises the gnu branches for real. The bsd
# branches are pinned through stub tools: little zsh scripts on a prepended
# PATH that behave the way the BSD spellings do (no --version, stat -f,
# sed -i '', date -r). A lying uname sits in the stub directory on purpose:
# some of our laptops report one OS name while carrying the other userland,
# so any library that asks the OS instead of the tool gets the wrong branch
# here and fails the contract checks below.
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL
unset CDPATH cdpath 2>/dev/null

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

zmodload zsh/mapfile

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
trap 'rm -rf "$ROOT/$T"' EXIT

typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

mkexp() { # mkexp <var> <line>... -- join lines with \n plus trailing newline
  local __n=$1
  shift
  : ${(P)__n::=${(F)@}$'\n'}
}

RC=0
OUT=''
ERR=''
run() { # run <cmd...> -- capture RC, OUT, ERR byte-exactly
  "$@" > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  OUT=${mapfile[$ROOT/$T/out]-}
  ERR=${mapfile[$ROOT/$T/err]-}
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f portwrap.zsh ]]; then
  print -r -- 'FAIL portwrap.zsh not found in the workspace root'
  exit 1
fi

# ---- fixture builders -----------------------------------------------------------

make_data() { # make_data <dir> -- notes.txt, 17 bytes, mtime pinned
  mkdir -p "$1"
  printf 'alpha: 1\nbeta: 2\n' > "$1/notes.txt"
  touch -d @1700000000 "$1/notes.txt"
}

# ---- stub BSD userland ----------------------------------------------------------

STUBS=$ROOT/$T/bsdbin
mkdir -p "$STUBS"
REAL_SED=$(command -v sed)

cat > "$STUBS/stat" <<'STUB'
#!/usr/bin/env zsh
# fixture stat, BSD-flavored: no --version, no -c; knows -f %m and -f %z.
emulate -R zsh
for a in "$@"; do
  if [[ $a == --version || $a == -c* ]]; then
    print -u2 -- "stat: illegal option -- ${${a#-}#-}"
    exit 1
  fi
done
if [[ ${1-} != -f ]] || (( $# != 3 )); then
  print -u2 -- 'usage: stat [-f format] file'
  exit 1
fi
zmodload -F zsh/stat b:zstat
case $2 in
  %m) zstat +mtime "$3" ;;
  %z) zstat +size "$3" ;;
  *) print -u2 -- "stat: bad format: $2"; exit 1 ;;
esac
STUB

cat > "$STUBS/sed" <<STUB
#!/usr/bin/env zsh
# fixture sed, BSD-flavored: no --version; -i takes a backup suffix argument,
# so the no-backup spelling is -i '' — a bare -i eats the next word as the
# suffix, exactly the classic porting trip-up.
emulate -R zsh
REAL_SED=${REAL_SED}
STUB
cat >> "$STUBS/sed" <<'STUB'
for a in "$@"; do
  if [[ $a == --version ]]; then
    print -u2 -- 'sed: illegal option -- -'
    exit 1
  fi
done
if [[ ${1-} != -i* ]]; then
  print -u2 -- 'sed: this fixture only supports -i mode'
  exit 1
fi
if [[ $1 == -i ]]; then
  suffix=${2-}
  shift 2
else
  suffix=${1#-i}
  shift 1
fi
if [[ ${1-} == -e ]]; then
  script=${2-}
  shift 2
else
  script=${1-}
  shift 1
fi
file=${1-}
if [[ -z $file || ! -f $file ]]; then
  print -u2 -- "sed: $file: No such file or directory"
  exit 1
fi
if [[ -n $suffix ]]; then
  cp -- "$file" "${file}${suffix}"
fi
"$REAL_SED" -e "$script" -- "$file" > "${file}.pwtmp" && mv -- "${file}.pwtmp" "$file"
STUB

cat > "$STUBS/date" <<'STUB'
#!/usr/bin/env zsh
# fixture date, BSD-flavored: no --version, no -d; epochs come in via -r.
emulate -R zsh
for a in "$@"; do
  case $a in
    --version) print -u2 -- 'date: illegal option -- -'; exit 1 ;;
    -d*) print -u2 -- 'usage: date [-u] [-r seconds] [+format]'; exit 1 ;;
  esac
done
typeset -i epoch=0
fmt='%+'
while (( $# )); do
  case $1 in
    -u) export TZ=UTC; shift ;;
    -r) epoch=$2; shift 2 ;;
    +*) fmt=${1#+}; shift ;;
    *) print -u2 -- "date: illegal time format"; exit 1 ;;
  esac
done
zmodload zsh/datetime
strftime "$fmt" "$epoch"
STUB

cat > "$STUBS/uname" <<'STUB'
#!/usr/bin/env zsh
# The laptops lie: OS name says one thing, the userland is another. Anything
# choosing a branch off uname instead of probing the tool loses here.
echo Linux
STUB

chmod +x "$STUBS/stat" "$STUBS/sed" "$STUBS/date" "$STUBS/uname"

# ---- child scripts (run once against the real tools, once against the stubs) ----

cat > "$T/run_flavors.zsh" <<'CHILD'
#!/usr/bin/env zsh
emulate -R zsh
setopt no_unset
source ./portwrap.zsh
stat_flavor
sed_flavor
date_flavor
CHILD

cat > "$T/run_workers.zsh" <<'CHILD'
#!/usr/bin/env zsh
emulate -R zsh
setopt no_unset
d=$1
source ./portwrap.zsh
file_mtime_epoch "$d/notes.txt"
file_size_bytes "$d/notes.txt"
date_from_epoch 0
date_from_epoch 1700000000
date_from_epoch 1234567890 '%Y-%m-%d %H:%M'
sed_inplace 's/alpha/omega/' "$d/notes.txt"
print -r -- '---after edit---'
print -r -- "$(<$d/notes.txt)"
CHILD

cat > "$T/run_cache.zsh" <<'CHILD'
#!/usr/bin/env zsh
emulate -R zsh
setopt no_unset
stubs=$1
source ./portwrap.zsh
stat_flavor
sed_flavor
date_flavor
path=("$stubs" $path)
stat_flavor
sed_flavor
date_flavor
portwrap_reset
stat_flavor
sed_flavor
date_flavor
CHILD

mkexp exp_workers \
  '1700000000' \
  '17' \
  '1970-01-01T00:00:00Z' \
  '2023-11-14T22:13:20Z' \
  '2009-02-13 23:31' \
  '---after edit---' \
  'omega: 1' \
  'beta: 2'

# ---- sourcing is quiet ----------------------------------------------------------

run zsh -f -c 'source ./portwrap.zsh'
expect 'sourcing prints nothing' 0 '' ''

# ---- real GNU userland: detectors and the full worker contract ------------------

mkexp exp_flav 'gnu' 'gnu' 'gnu'
run zsh -f "$T/run_flavors.zsh"
expect 'flavors on the real userland' 0 "$exp_flav" ''

make_data "$T/d1"
run zsh -f "$T/run_workers.zsh" "$T/d1"
expect 'workers on the real userland' 0 "$exp_workers" ''

leftover=( "$T"/d1/*(N) )
assert_eq 'gnu edit leaves no extra files behind' "$T/d1/notes.txt" "${(j:|:)leftover}"

# ---- stub BSD userland: same contract, byte for byte ----------------------------

mkexp exp_flav 'bsd' 'bsd' 'bsd'
run env PATH="$STUBS:$PATH" zsh -f "$T/run_flavors.zsh"
expect 'flavors through the stub userland' 0 "$exp_flav" ''

make_data "$T/d2"
run env PATH="$STUBS:$PATH" zsh -f "$T/run_workers.zsh" "$T/d2"
expect 'workers through the stub userland' 0 "$exp_workers" ''

leftover=( "$T"/d2/*(N) )
assert_eq 'bsd edit leaves no backup files behind' "$T/d2/notes.txt" "${(j:|:)leftover}"

# ---- probe once, remember, forget on request ------------------------------------

mkexp exp_cache 'gnu' 'gnu' 'gnu' 'gnu' 'gnu' 'gnu' 'bsd' 'bsd' 'bsd'
run zsh -f "$T/run_cache.zsh" "$STUBS"
expect 'flavors are cached until portwrap_reset' 0 "$exp_cache" ''

# ---- UTC discipline: caller timezone must not leak into the output --------------

make_data "$T/d3"
run env TZ=America/New_York zsh -f -c 'source ./portwrap.zsh; date_from_epoch 0'
expect 'epoch rendering ignores caller TZ' 0 $'1970-01-01T00:00:00Z\n' ''

# ---- callers with unusual option sets still get correct answers -----------------

mkdir -p "$T/d4"
printf 'alpha: 1\nbeta: 2\n' > "$T/d4/two words.txt"
run zsh -f -c 'setopt shwordsplit ksharrays nomatch; source ./portwrap.zsh; file_size_bytes "_t/d4/two words.txt"; date_from_epoch 0'
expect 'caller option soup does not break the library' 0 $'17\n1970-01-01T00:00:00Z\n' ''

# ---- error contract --------------------------------------------------------------

run zsh -f -c 'source ./portwrap.zsh; file_mtime_epoch _t/absent.txt'
expect 'mtime of a missing file' 1 '' $'portwrap: no such file: _t/absent.txt\n'

run zsh -f -c 'source ./portwrap.zsh; file_size_bytes _t/absent.txt'
expect 'size of a missing file' 1 '' $'portwrap: no such file: _t/absent.txt\n'

run zsh -f -c 'source ./portwrap.zsh; sed_inplace s/a/b/ _t/absent.txt'
expect 'in-place edit of a missing file' 1 '' $'portwrap: no such file: _t/absent.txt\n'

run zsh -f -c 'source ./portwrap.zsh; date_from_epoch 17cows'
expect 'non-numeric epoch' 1 '' $'portwrap: not an epoch: 17cows\n'

# ---- summary ----------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
