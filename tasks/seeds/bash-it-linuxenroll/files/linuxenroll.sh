#!/usr/bin/env bash

set -euo pipefail

readonly SERVICE_NAME="moon-management"
readonly LOGGING_RULE='local5.*    /var/log/linuxenroll-events.log'
readonly -a BASELINE_PACKAGES=(
  ca-certificates
  curl
  jq
  rsyslog
)

usage() {
  cat >&2 <<'EOF'
usage:
  linuxenroll.sh enroll --root ROOT --certificate ABSOLUTE_PATH --owner KEY=VALUE [--owner KEY=VALUE ...]
  linuxenroll.sh unenroll --root ROOT
EOF
}

die() {
  printf 'linuxenroll: %s\n' "$*" >&2
  exit 2
}

validate_root() {
  local root=$1
  local resolved
  [[ $root == /* ]] || die "--root must be an absolute path"
  if ! resolved=$(realpath -e -- "$root" 2>/dev/null); then
    die "root does not exist: $root"
  fi
  [[ -d $resolved ]] || die "root is not a directory: $root"
  [[ $resolved != / ]] || die "refusing to use the real filesystem root"
  ROOT_VALUE=$resolved
}

read_identity() {
  local root=$1
  local hostname_file="$root/etc/hostname"
  local machine_id_file="$root/etc/machine-id"

  [[ -f $hostname_file ]] || die "missing host identity: /etc/hostname"
  [[ -f $machine_id_file ]] || die "missing host identity: /etc/machine-id"

  IFS= read -r HOSTNAME_VALUE < "$hostname_file" || true
  IFS= read -r MACHINE_ID_VALUE < "$machine_id_file" || true

  [[ $HOSTNAME_VALUE =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
    die "invalid hostname"
  [[ $MACHINE_ID_VALUE =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] ||
    die "invalid machine-id"
}

validate_certificate() {
  local root=$1
  local certificate=$2
  local resolved

  [[ $certificate == /* ]] || die "--certificate must be an absolute path"
  [[ $certificate != *'/../'* && $certificate != */.. && $certificate != /.. ]] ||
    die "--certificate may not contain '..' path components"
  if ! resolved=$(realpath -e -- "$root$certificate" 2>/dev/null); then
    die "certificate does not exist below root: $certificate"
  fi
  [[ -f $resolved && $resolved == "$root/"* ]] ||
    die "certificate does not exist below root: $certificate"
}

canonicalize_owners() {
  local owner key value
  declare -A seen_keys=()
  local -a checked=()

  ((${#OWNER_VALUES[@]} > 0)) || die "at least one --owner tag is required"

  for owner in "${OWNER_VALUES[@]}"; do
    [[ $owner == *=* ]] || die "owner tag must be KEY=VALUE: $owner"
    key=${owner%%=*}
    value=${owner#*=}
    [[ $key =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
      die "invalid owner tag key: $key"
    [[ -n $value && $value != *$'\n'* && $value != *$'\r'* ]] ||
      die "owner tag value must be non-empty and single-line: $key"
    [[ ! -v "seen_keys[$key]" ]] || die "duplicate owner tag key: $key"
    seen_keys[$key]=1
    checked+=("$key=$value")
  done

  mapfile -t CANONICAL_OWNERS < <(printf '%s\n' "${checked[@]}" | LC_ALL=C sort)
}

write_managed_file() {
  local destination=$1
  local mode=$2
  local temporary="${destination}.tmp"

  umask 077
  cat > "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$destination"
}

desired_hash() {
  {
    printf 'service=%s\n' "$SERVICE_NAME"
    printf 'hostname=%s\n' "$HOSTNAME_VALUE"
    printf 'machine_id=%s\n' "$MACHINE_ID_VALUE"
    printf 'certificate=%s\n' "$CERTIFICATE_VALUE"
    printf 'package=%s\n' "${BASELINE_PACKAGES[@]}"
    printf 'logging=%s\n' "$LOGGING_RULE"
  } | sha256sum | cut -d' ' -f1
}

enroll() {
  local root=""
  CERTIFICATE_VALUE=""
  OWNER_VALUES=()

  while (($#)); do
    case $1 in
      --root)
        (($# >= 2)) || die "--root requires a value"
        root=${2%/}
        shift 2
        ;;
      --certificate)
        (($# >= 2)) || die "--certificate requires a value"
        CERTIFICATE_VALUE=$2
        shift 2
        ;;
      --owner)
        (($# >= 2)) || die "--owner requires a value"
        OWNER_VALUES+=("$2")
        shift 2
        ;;
      *)
        die "unknown enroll argument: $1"
        ;;
    esac
  done

  [[ -n $root ]] || die "--root is required"
  [[ -n $CERTIFICATE_VALUE ]] || die "--certificate is required"
  validate_root "$root"
  root=$ROOT_VALUE
  read_identity "$root"
  validate_certificate "$root" "$CERTIFICATE_VALUE"
  canonicalize_owners

  local state_dir="$root/var/lib/linuxenroll"
  local config_dir="$root/etc/linuxenroll"
  local service_dir="$root/var/lib/$SERVICE_NAME/hosts"
  local rsyslog_dir="$root/etc/rsyslog.d"
  local log_dir="$root/var/log"
  local fingerprint_file="$state_dir/desired.sha256"
  local fingerprint
  fingerprint=$(desired_hash)

  if [[ -f $fingerprint_file ]] && [[ $(< "$fingerprint_file") == "$fingerprint" ]]; then
    printf 'already enrolled\n'
    return 0
  fi

  local event=enrolled
  [[ -f "$state_dir/enrollment.conf" ]] && event=updated

  mkdir -p "$state_dir" "$config_dir" "$service_dir" "$rsyslog_dir" "$log_dir"

  {
    printf 'service=%s\n' "$SERVICE_NAME"
    printf 'hostname=%s\n' "$HOSTNAME_VALUE"
    printf 'machine_id=%s\n' "$MACHINE_ID_VALUE"
    printf 'certificate=%s\n' "$CERTIFICATE_VALUE"
  } | write_managed_file "$state_dir/enrollment.conf" 600

  printf '%s\n' "${BASELINE_PACKAGES[@]}" |
    write_managed_file "$config_dir/baseline-packages" 644
  printf '%s\n' "${CANONICAL_OWNERS[@]}" |
    write_managed_file "$config_dir/owner.tags" 600
  printf '%s\n' "$LOGGING_RULE" |
    write_managed_file "$rsyslog_dir/60-linuxenroll.conf" 644

  {
    printf 'service=%s\n' "$SERVICE_NAME"
    printf 'hostname=%s\n' "$HOSTNAME_VALUE"
    printf 'machine_id=%s\n' "$MACHINE_ID_VALUE"
    printf 'certificate=%s\n' "$CERTIFICATE_VALUE"
    printf 'package=%s\n' "${BASELINE_PACKAGES[@]}"
    printf 'owner=%s\n' "${CANONICAL_OWNERS[@]}"
  } | write_managed_file "$service_dir/$MACHINE_ID_VALUE.record" 600

  printf '%s\n' "$fingerprint" |
    write_managed_file "$fingerprint_file" 600
  printf '%s host=%s machine_id=%s\n' \
    "$event" "$HOSTNAME_VALUE" "$MACHINE_ID_VALUE" >> "$log_dir/linuxenroll.log"
  chmod 600 "$log_dir/linuxenroll.log"

  printf '%s\n' "$event"
}

unenroll() {
  local root=""

  while (($#)); do
    case $1 in
      --root)
        (($# >= 2)) || die "--root requires a value"
        root=${2%/}
        shift 2
        ;;
      *)
        die "unknown unenroll argument: $1"
        ;;
    esac
  done

  [[ -n $root ]] || die "--root is required"
  validate_root "$root"
  root=$ROOT_VALUE

  local state_dir="$root/var/lib/linuxenroll"
  local enrollment_file="$state_dir/enrollment.conf"
  if [[ ! -f $enrollment_file ]]; then
    printf 'already unenrolled\n'
    return 0
  fi

  local enrolled_hostname=""
  local enrolled_machine_id=""
  local key value
  while IFS='=' read -r key value; do
    case $key in
      hostname) enrolled_hostname=$value ;;
      machine_id) enrolled_machine_id=$value ;;
    esac
  done < "$enrollment_file"

  [[ $enrolled_hostname =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
    die "stored enrollment has an invalid hostname"
  [[ $enrolled_machine_id =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] ||
    die "stored enrollment has an invalid machine-id"

  rm -f \
    "$state_dir/enrollment.conf" \
    "$state_dir/desired.sha256" \
    "$root/etc/linuxenroll/baseline-packages" \
    "$root/etc/linuxenroll/owner.tags" \
    "$root/etc/rsyslog.d/60-linuxenroll.conf" \
    "$root/var/lib/$SERVICE_NAME/hosts/$enrolled_machine_id.record"

  rmdir "$state_dir" "$root/var/lib/$SERVICE_NAME/hosts" \
    "$root/var/lib/$SERVICE_NAME" 2>/dev/null || true
  rmdir "$root/etc/rsyslog.d" "$root/etc/linuxenroll" 2>/dev/null || true

  mkdir -p "$root/var/log"
  printf 'unenrolled host=%s machine_id=%s\n' \
    "$enrolled_hostname" "$enrolled_machine_id" >> "$root/var/log/linuxenroll.log"
  chmod 600 "$root/var/log/linuxenroll.log"
  printf 'unenrolled\n'
}

main() {
  (($# > 0)) || {
    usage
    exit 2
  }

  local command=$1
  shift
  case $command in
    enroll) enroll "$@" ;;
    unenroll) unenroll "$@" ;;
    *)
      usage
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
