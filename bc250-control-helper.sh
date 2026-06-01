#!/usr/bin/env bash
#
# Privileged helper for BC-250 Control Center.
# This script intentionally exposes a tiny command surface for the GUI.

set -u

CARD="${BC250_CARD:-card1}"
CARD_PATH="/sys/class/drm/${CARD}/device"
DEV="${CARD_PATH}/pp_od_clk_voltage"
PIDFILE="/run/bc250-uv-loop.pid"
SESSION_READY="__BC250_READY__"
SESSION_DONE_PREFIX="__BC250_DONE__:"

die() {
  echo "Error: $*" >&2
  exit 1
}

require_root() {
  [ "${EUID:-$(id -u)}" -eq 0 ] || die "run this helper as root"
}

require_device() {
  [ -e "$DEV" ] || die "$DEV does not exist"
}

is_integer() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

validate_range() {
  local value="$1"
  local min="$2"
  local max="$3"
  local name="$4"

  is_integer "$value" || die "$name must be an integer"
  [ "$value" -ge "$min" ] || die "$name must be >= $min"
  [ "$value" -le "$max" ] || die "$name must be <= $max"
}

stop_loop() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
  fi

  pkill -f "bc250-uv-mode-loop" 2>/dev/null || true
}

apply_loop() {
  local mhz="$1"
  local mv="$2"
  local interval="$3"

  validate_range "$mhz" 1000 2000 "MHz"
  validate_range "$mv" 700 1129 "mV"
  validate_range "$interval" 1 60 "Interval"

  stop_loop

  (
    exec -a bc250-uv-mode-loop bash -c '
      DEV="$1"
      MHZ="$2"
      MV="$3"
      INTERVAL="$4"
      while true; do
        if [ -e "$DEV" ]; then
          echo "vc 0 ${MHZ} ${MV}" > "$DEV" 2>/dev/null
          echo c > "$DEV" 2>/dev/null
        fi
        sleep "$INTERVAL"
      done
    ' _ "$DEV" "$mhz" "$mv" "$interval"
  ) >/dev/null 2>&1 &

  echo $! > "$PIDFILE"
  echo "Applied ${mhz} MHz / ${mv} mV, loop every ${interval}s"
}

reset_od() {
  stop_loop
  echo "r" > "$DEV" 2>/dev/null || die "failed to reset OD table"

  if [ -w "$CARD_PATH/power_dpm_force_performance_level" ]; then
    echo "auto" > "$CARD_PATH/power_dpm_force_performance_level" 2>/dev/null || true
  fi

  echo "Reset OD table request sent"
}

usage() {
  cat <<EOF
Usage:
  $0 [--card cardN] apply <mhz> <mv> [interval_seconds]
  $0 [--card cardN] stop
  $0 [--card cardN] reset
  $0 [--card cardN] session

Environment:
  BC250_CARD=card1
EOF
}

run_session_command() {
  local action="${1:-}"
  local output=""
  local status=0

  case "$action" in
    apply)
      if [ "$#" -lt 3 ]; then
        output="Error: apply requires MHz and mV"
        status=2
      else
        output="$(apply_loop "$2" "$3" "${4:-5}" 2>&1)" || status=$?
      fi
      ;;
    stop)
      output="$(stop_loop 2>&1 && echo "Loop stopped")" || status=$?
      ;;
    reset)
      output="$(reset_od 2>&1)" || status=$?
      ;;
    quit|exit)
      echo "Session closing"
      echo "${SESSION_DONE_PREFIX}0"
      return 1
      ;;
    "")
      output="Error: empty command"
      status=2
      ;;
    *)
      output="Error: unsupported session command: $action"
      status=2
      ;;
  esac

  [ -n "$output" ] && printf '%s\n' "$output"
  echo "${SESSION_DONE_PREFIX}${status}"
  return 0
}

session_loop() {
  local line

  echo "BC-250 helper session ready for ${CARD}"
  echo "$SESSION_READY"

  while IFS= read -r line; do
    # Session commands only use numeric values and fixed command words.
    # shellcheck disable=SC2086
    set -- $line
    run_session_command "$@" || break
  done
}

main() {
  if [ "${1:-}" = "--card" ]; then
    [ -n "${2:-}" ] || die "--card requires a card name, for example card1"
    CARD="$2"
    CARD_PATH="/sys/class/drm/${CARD}/device"
    DEV="${CARD_PATH}/pp_od_clk_voltage"
    shift 2
  fi

  case "${1:-}" in
    -h|--help|help)
      usage
      exit 0
      ;;
  esac

  require_root
  require_device

  case "${1:-}" in
    apply)
      [ "$#" -ge 3 ] || die "apply requires MHz and mV"
      apply_loop "$2" "$3" "${4:-5}"
      ;;
    stop)
      stop_loop
      echo "Loop stopped"
      ;;
    reset)
      reset_od
      ;;
    session)
      session_loop
      ;;
    *)
      usage
      exit 2
      ;;
  esac
}

main "$@"
