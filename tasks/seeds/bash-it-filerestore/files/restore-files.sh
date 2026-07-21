#!/usr/bin/env bash

set -u
set -o pipefail

program=${0##*/}
overwrite=0
backup_root=
destination_root=
stage_root=
rollback_root=
transaction_active=0
declare -a committed=()
declare -a replaced=()
declare -a created_directories=()
declare -A checksums=()
declare -A selections=()

usage() {
    printf 'usage: %s [--overwrite] BACKUP_DIR DESTINATION_DIR PATH...\n' "$program" >&2
}

fail() {
    printf '%s: %s\n' "$program" "$*" >&2
    exit 1
}

validate_relative_path() {
    local path=$1

    [[ -n $path && $path != /* ]] || return 1
    [[ $path != *$'\n'* && $path != *$'\r'* && $path != *$'\t'* ]] || return 1
    case "/$path/" in
        *'//'*|*'/./'*|*'/../'*) return 1 ;;
    esac
    return 0
}

load_manifest() {
    local manifest=$backup_root/manifest.sha256
    local line digest separator path

    [[ -f $manifest && ! -L $manifest ]] || fail "missing regular checksum manifest"
    while IFS= read -r line || [[ -n $line ]]; do
        [[ -n $line ]] || continue
        ((${#line} >= 67)) || fail "malformed checksum manifest"
        digest=${line:0:64}
        separator=${line:64:2}
        path=${line:66}
        [[ $digest =~ ^[[:xdigit:]]{64}$ && $separator == '  ' ]] ||
            fail "malformed checksum manifest"
        validate_relative_path "$path" || fail "unsafe path in checksum manifest: $path"
        [[ -z ${checksums[$path]+present} ]] || fail "duplicate checksum entry: $path"
        checksums["$path"]=${digest,,}
    done < "$manifest"
}

validate_source_file() {
    local relative=$1
    local current=$backup_root/payload
    local component
    local -a components

    IFS=/ read -r -a components <<< "$relative"
    for component in "${components[@]}"; do
        current=$current/$component
        [[ ! -L $current ]] || return 1
    done
    [[ -f $current ]]
}

checksum_of() {
    local output
    output=$(sha256sum -- "$1") || return 1
    printf '%s\n' "${output%% *}"
}

stage_selection() {
    local relative=$1
    local source=$backup_root/payload/$relative
    local staged=$stage_root/$relative
    local actual

    validate_source_file "$relative" || fail "backup path is not a safe regular file: $relative"
    [[ -n ${checksums[$relative]+present} ]] || fail "no checksum for selected path: $relative"
    mkdir -p -- "${staged%/*}" || fail "cannot create staging directory"
    cp -p -- "$source" "$staged" || fail "cannot stage selected path: $relative"
    actual=$(checksum_of "$staged") || fail "cannot checksum selected path: $relative"
    [[ $actual == "${checksums[$relative]}" ]] || fail "checksum mismatch: $relative"
}

prepare_destination_parent() {
    local relative=$1
    local parent current component
    local -a components

    [[ $relative == */* ]] || return 0
    parent=${relative%/*}
    current=$destination_root
    IFS=/ read -r -a components <<< "$parent"
    for component in "${components[@]}"; do
        current=$current/$component
        [[ ! -L $current ]] || return 1
        if [[ -e $current ]]; then
            [[ -d $current ]] || return 1
        else
            mkdir -- "$current" || return 1
            created_directories+=("$current")
        fi
    done
}

commit_selection() {
    local relative=$1
    local target=$destination_root/$relative
    local saved=$rollback_root/$relative
    local actual

    prepare_destination_parent "$relative" || return 1
    [[ ! -L $target ]] || return 1
    if [[ -e $target ]]; then
        [[ $overwrite -eq 1 && -f $target ]] || return 1
        mkdir -p -- "${saved%/*}" || return 1
        mv -- "$target" "$saved" || return 1
        replaced+=("$relative")
    fi

    mv -- "$stage_root/$relative" "$target" || return 1
    committed+=("$relative")
    actual=$(checksum_of "$target") || return 1
    [[ $actual == "${checksums[$relative]}" ]]
}

rollback_transaction() {
    local index relative target saved

    for ((index=${#committed[@]}-1; index >= 0; index--)); do
        relative=${committed[index]}
        target=$destination_root/$relative
        rm -f -- "$target" || :
    done

    for ((index=${#replaced[@]}-1; index >= 0; index--)); do
        relative=${replaced[index]}
        saved=$rollback_root/$relative
        if [[ $relative == */* ]]; then
            mkdir -p -- "${destination_root}/${relative%/*}" || :
        fi
        mv -- "$saved" "$destination_root/${relative##*/}" || :
    done

    for ((index=${#created_directories[@]}-1; index >= 0; index--)); do
        rmdir -- "${created_directories[index]}" 2>/dev/null || :
    done
}

cleanup() {
    [[ -z $stage_root || ! -d $stage_root ]] || rm -rf -- "$stage_root"
    [[ -z $rollback_root || ! -d $rollback_root ]] || rm -rf -- "$rollback_root"
}

on_exit() {
    local status=$?
    trap - EXIT HUP INT TERM
    if [[ $status -ne 0 && $transaction_active -eq 1 ]]; then
        rollback_transaction
    fi
    cleanup
    exit "$status"
}

trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ${1-} == --overwrite ]]; then
    overwrite=1
    shift
elif [[ ${1-} == -- ]]; then
    shift
fi

[[ $# -ge 3 ]] || {
    usage
    exit 2
}

backup_argument=$1
destination_argument=$2
shift 2

[[ -d $backup_argument ]] || fail "backup directory does not exist"
[[ -d $destination_argument ]] || fail "destination directory does not exist"
backup_root=$(cd -- "$backup_argument" && pwd -P) || fail "cannot resolve backup directory"
destination_root=$(cd -- "$destination_argument" && pwd -P) || fail "cannot resolve destination directory"
[[ $backup_root != "$destination_root" ]] || fail "backup and destination must differ"
[[ -d $backup_root/payload && ! -L $backup_root/payload ]] || fail "missing backup payload"

load_manifest

selected_paths=("$@")
for relative in "${selected_paths[@]}"; do
    validate_relative_path "$relative" || fail "unsafe selected path: $relative"
    [[ -z ${selections[$relative]+present} ]] || fail "duplicate selected path: $relative"
    selections["$relative"]=1
done

stage_root=$(mktemp -d "$destination_root/.filerestore-stage.XXXXXX") || fail "cannot create staging area"
rollback_root=$(mktemp -d "$destination_root/.filerestore-rollback.XXXXXX") || fail "cannot create rollback area"
transaction_active=1

for relative in "${selected_paths[@]}"; do
    stage_selection "$relative"
done

for relative in "${selected_paths[@]}"; do
    commit_selection "$relative" || fail "cannot install selected path: $relative"
done

transaction_active=0
exit 0
