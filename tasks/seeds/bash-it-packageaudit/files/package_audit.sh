#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

validate_version() {
  [[ $1 =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || die "invalid version: $1"
}

version_cmp() {
  local left=$1 right=$2
  local l1=0 l2=0 l3=0 r1=0 r2=0 r3=0
  validate_version "$left"
  validate_version "$right"
  IFS=. read -r l1 l2 l3 <<<"$left"
  IFS=. read -r r1 r2 r3 <<<"$right"
  l2=${l2:-0}; l3=${l3:-0}; r2=${r2:-0}; r3=${r3:-0}

  local lpart rpart
  for lpart in "$l1" "$l2" "$l3"; do
    [[ $lpart =~ ^[0-9]+$ ]] || die "invalid version: $left"
  done
  for rpart in "$r1" "$r2" "$r3"; do
    [[ $rpart =~ ^[0-9]+$ ]] || die "invalid version: $right"
  done

  if ((10#$l1 != 10#$r1)); then
    ((10#$l1 < 10#$r1)) && printf '%s\n' -1 || printf '%s\n' 1
  elif ((10#$l2 != 10#$r2)); then
    ((10#$l2 < 10#$r2)) && printf '%s\n' -1 || printf '%s\n' 1
  elif ((10#$l3 != 10#$r3)); then
    ((10#$l3 < 10#$r3)) && printf '%s\n' -1 || printf '%s\n' 1
  else
    printf '%s\n' 0
  fi
}

version_lt() { [[ $(version_cmp "$1" "$2") == -1 ]]; }
version_le() { [[ $(version_cmp "$1" "$2") != 1 ]]; }
version_gt() { [[ $(version_cmp "$1" "$2") == 1 ]]; }
version_ge() { [[ $(version_cmp "$1" "$2") != -1 ]]; }

constraint_satisfied() {
  local version=$1 operator=$2 bound=$3
  case $operator in
    '<')  version_le "$version" "$bound" ;;
    '<=') version_le "$version" "$bound" ;;
    '>')  version_gt "$version" "$bound" ;;
    '>=') version_ge "$version" "$bound" ;;
    '=')  [[ $(version_cmp "$version" "$bound") == 0 ]] ;;
    *)    die "unsupported constraint operator: $operator" ;;
  esac
}

declare -a package_order=()
declare -a dependencies=()
declare -A installed=()
declare -A repository=()
declare -A pin_operator=()
declare -A pin_bound=()
declare -A pin_reason=()
declare -A decision=()
declare -A detail=()
declare -A projected=()

load_fixtures() {
  local fixture_dir=$1 package version extra operator bound reason
  local package_version dependency
  for required in installed.tsv repository.tsv pins.tsv dependencies.tsv; do
    [[ -f $fixture_dir/$required ]] || die "missing fixture: $fixture_dir/$required"
  done

  while IFS=$'\t' read -r package version extra || [[ -n ${package:-} ]]; do
    [[ -z $package || $package == \#* ]] && continue
    [[ -n $version && -z ${extra:-} ]] || die "invalid installed row for $package"
    validate_version "$version"
    [[ -z ${installed[$package]+present} ]] || die "duplicate installed package: $package"
    installed[$package]=$version
    package_order+=("$package")
  done < "$fixture_dir/installed.tsv"

  while IFS=$'\t' read -r package version extra || [[ -n ${package:-} ]]; do
    [[ -z $package || $package == \#* ]] && continue
    [[ -n $version && -z ${extra:-} ]] || die "invalid repository row for $package"
    validate_version "$version"
    repository[$package]=$version
  done < "$fixture_dir/repository.tsv"

  while IFS=$'\t' read -r package operator bound reason extra || [[ -n ${package:-} ]]; do
    [[ -z $package || $package == \#* ]] && continue
    [[ -n $operator && -n $bound && -n $reason && -z ${extra:-} ]] || die "invalid pin row for $package"
    validate_version "$bound"
    pin_operator[$package]=$operator
    pin_bound[$package]=$bound
    pin_reason[$package]=$reason
  done < "$fixture_dir/pins.tsv"

  while IFS=$'\t' read -r package package_version dependency operator bound extra || [[ -n ${package:-} ]]; do
    [[ -z $package || $package == \#* ]] && continue
    [[ -n $package_version && -n $dependency && -n $operator && -n $bound && -z ${extra:-} ]] || die "invalid dependency row for $package"
    validate_version "$package_version"
    validate_version "$bound"
    dependencies+=("$package"$'\t'"$package_version"$'\t'"$dependency"$'\t'"$operator"$'\t'"$bound")
  done < "$fixture_dir/dependencies.tsv"
}

major_of() {
  printf '%s\n' "${1%%.*}"
}

build_plan() {
  local package current candidate operator bound dep_row package_version dependency
  local changed dep_version

  for package in "${package_order[@]}"; do
    current=${installed[$package]}
    candidate=${repository[$package]-}
    projected[$package]=$current
    if [[ -z $candidate ]]; then
      decision[$package]=current
      detail[$package]='not present in repository'
    elif ! version_gt "$candidate" "$current"; then
      decision[$package]=current
      detail[$package]='repository has no newer version'
    elif [[ -n ${pin_operator[$package]-} ]] && ! constraint_satisfied "$candidate" "${pin_operator[$package]}" "${pin_bound[$package]}"; then
      decision[$package]=pin
      detail[$package]="pin requires ${pin_operator[$package]} ${pin_bound[$package]} (${pin_reason[$package]})"
    elif [[ $(major_of "$candidate") != $(major_of "$current") && -z ${pin_operator[$package]-} ]]; then
      decision[$package]=major
      detail[$package]='major-version change has no explicit pin authorization'
    else
      decision[$package]=upgrade
      detail[$package]=''
      projected[$package]=$candidate
    fi
  done

  changed=1
  while ((changed)); do
    changed=0
    for package in "${package_order[@]}"; do
      [[ ${decision[$package]} == upgrade ]] || continue
      for dep_row in "${dependencies[@]}"; do
        IFS=$'\t' read -r package_version candidate dependency operator bound <<<"$dep_row"
        [[ $package_version == "$package" && $candidate == "${repository[$package]}" ]] || continue
        dep_version=${projected[$dependency]-}
        if [[ -z $dep_version ]] || ! constraint_satisfied "$dep_version" "$operator" "$bound"; then
          decision[$package]=dependency
          detail[$package]="requires $dependency $operator $bound, projected ${dep_version:-missing}"
          projected[$package]=${installed[$package]}
          changed=1
          break
        fi
      done
    done
  done
}

print_audit() {
  local package
  for package in "${package_order[@]}"; do
    case ${decision[$package]} in
      upgrade)
        printf 'UPGRADE %s %s -> %s\n' "$package" "${installed[$package]}" "${repository[$package]}"
        ;;
      current)
        printf 'CURRENT %s %s: %s\n' "$package" "${installed[$package]}" "${detail[$package]}"
        ;;
      *)
        printf 'HOLD %s %s -> %s: %s\n' "$package" "${installed[$package]}" "${repository[$package]}" "${detail[$package]}"
        ;;
    esac
  done
}

print_plan() {
  local package
  for package in "${package_order[@]}"; do
    [[ ${decision[$package]} == upgrade ]] || continue
    printf 'PLAN %s %s -> %s\n' "$package" "${installed[$package]}" "${repository[$package]}"
  done
}

simulate_plan() {
  local package
  for package in "${package_order[@]}"; do
    [[ ${decision[$package]} == upgrade ]] || continue
    printf 'WOULD_APPLY %s %s -> %s\n' "$package" "${installed[$package]}" "${projected[$package]}"
  done
}

verify_plan() {
  local package dep_row owner owner_version dependency operator bound dep_version count=0
  for package in "${package_order[@]}"; do
    [[ ${decision[$package]} == upgrade ]] || continue
    ((count += 1))
    if [[ $(major_of "${installed[$package]}") != $(major_of "${projected[$package]}") ]]; then
      if [[ -z ${pin_operator[$package]-} ]] || ! constraint_satisfied "${projected[$package]}" "${pin_operator[$package]}" "${pin_bound[$package]}"; then
        printf 'VERIFY_FAILED unauthorized major-version change: %s %s -> %s\n' \
          "$package" "${installed[$package]}" "${projected[$package]}"
        return 1
      fi
    fi
  done

  for dep_row in "${dependencies[@]}"; do
    IFS=$'\t' read -r owner owner_version dependency operator bound <<<"$dep_row"
    [[ ${projected[$owner]-} == "$owner_version" ]] || continue
    dep_version=${projected[$dependency]-}
    if [[ -z $dep_version ]] || ! constraint_satisfied "$dep_version" "$operator" "$bound"; then
      printf 'VERIFY_FAILED dependency: %s %s requires %s %s %s\n' \
        "$owner" "$owner_version" "$dependency" "$operator" "$bound"
      return 1
    fi
  done

  printf 'VERIFIED %d planned upgrade(s); no unauthorized major-version changes\n' "$count"
}

usage() {
  printf 'usage: %s {audit|plan|simulate|verify|all} [fixture-directory]\n' "${0##*/}" >&2
  exit 2
}

main() {
  (($# >= 1 && $# <= 2)) || usage
  local command=$1
  local fixture_dir=${2:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fixtures"}
  load_fixtures "$fixture_dir"
  build_plan

  case $command in
    audit) print_audit ;;
    plan) print_plan ;;
    simulate) simulate_plan ;;
    verify) verify_plan ;;
    all)
      printf '%s\n' AUDIT
      print_audit
      printf '%s\n' PLAN
      print_plan
      printf '%s\n' SIMULATION
      simulate_plan
      printf '%s\n' VERIFICATION
      verify_plan
      ;;
    *) usage ;;
  esac
}

main "$@"
