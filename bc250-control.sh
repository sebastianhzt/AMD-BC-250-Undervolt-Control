#!/usr/bin/env bash
#
# AMD BC-250 Undervolt Control Menu (single self-contained script)
# Modes:
#  1) Gaming mode      - loop 2000 MHz / 925 mV
#  2) Balanced mode    - loop 1500 MHz / 810 mV
#  3) Power saving     - loop 1000 MHz / 700 mV
#  4) Stop loop        - stop writing undervolt values
#  5) Reset OD table   - amdgpu reset (may not restore stock boost on BC-250)
#  6) Real-time monitor
#
# NOTE:
# - This script assumes the BC-250 is exposed as /sys/class/drm/card1
# - Requires amdgpu OverDrive/PP features enabled (e.g. amdgpu.ppfeaturemask=0xffffffff)
# - Use at your own risk. Undervolting/overclocking can cause instability or damage.

DEV="/sys/class/drm/card1/device/pp_od_clk_voltage"
CARD_PATH="/sys/class/drm/card1/device"
DEBUG_PM_INFO="/sys/kernel/debug/dri/1/amdgpu_pm_info"
PIDFILE="/run/bc250-uv-loop.pid"

require_root() {
  if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo:"
    echo "  sudo ./bc250-control.sh"
    exit 1
  fi
}

wait_for_device() {
  while [ ! -e "$DEV" ]; do
    echo "Waiting for $DEV ..."
    sleep 2
  done
}

stop_loop() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Stopping undervolt loop (PID $PID) ..."
      kill "$PID" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
  fi

  pkill -f "bc250-uv-mode-loop" 2>/dev/null || true
}

start_loop() {
  local mhz="$1"
  local mv="$2"

  stop_loop

  echo "Starting undervolt loop: ${mhz} MHz / ${mv} mV ..."
  (
    echo "BC-250 undervolt mode loop started: ${mhz} MHz / ${mv} mV" >&2
    exec -a bc250-uv-mode-loop bash -c '
      DEV="'"$DEV"'"
      MHZ="'"$mhz"'"
      MV="'"$mv"'"
      while true; do
        if [ -e "$DEV" ]; then
          echo "vc 0 ${MHZ} ${MV}" > "$DEV" 2>/dev/null
          echo c                   > "$DEV" 2>/dev/null
        fi
        sleep 5
      done
    '
  ) &
  echo $! > "$PIDFILE"
  echo "Loop started with PID $(cat "$PIDFILE")."
}

gaming_mode() {
  echo "[Gaming mode] 2000 MHz / 925 mV (loop)"
  start_loop 2000 925
}

balanced_mode() {
  echo "[Balanced mode] 1500 MHz / 810 mV (loop)"
  start_loop 1500 810
}

power_saving_mode() {
  echo "[Power saving mode] 1000 MHz / 700 mV (loop)"
  start_loop 1000 700
}

stop_undervolt_loop() {
  echo "[Stop loop] Stopping undervolt loop only..."
  stop_loop
  echo "No undervolt loop is running now."
  echo
  echo "Current OD state:"
  cat "$DEV"
}

reset_od_table() {
  echo "[Reset OD table] Stopping loops and asking amdgpu to reset OD..."
  echo
  echo "Warning: on BC-250 this may keep the last manual SCLK/VDDC point."
  echo "This is not guaranteed to restore the original dynamic stock boost table."
  echo
  stop_loop
  echo "r" > "$DEV" 2>/dev/null
  if [ -w "$CARD_PATH/power_dpm_force_performance_level" ]; then
    echo "auto" > "$CARD_PATH/power_dpm_force_performance_level" 2>/dev/null || true
  fi
  sleep 1
  echo "Current OD state:"
  cat "$DEV"
}

find_hwmon_path() {
  local h
  for h in "$CARD_PATH"/hwmon/hwmon*; do
    [ -d "$h" ] && echo "$h" && return
  done
}

get_gpu_clock() {
  local f

  f="$CARD_PATH/freq1_input"
  if [ -r "$f" ]; then
    awk '{printf "%.0f MHz", $1/1000000}' "$f" 2>/dev/null
    return
  fi

  f="$CARD_PATH/gt_cur_freq_mhz"
  if [ -r "$f" ]; then
    awk '{print $1 " MHz"}' "$f" 2>/dev/null
    return
  fi

  f="$CARD_PATH/pp_dpm_sclk"
  if [ -r "$f" ]; then
    awk '/\*/ {gsub("Mhz","",$2); print $2 " MHz"}' "$f" 2>/dev/null
    return
  fi

  echo "Not available"
}

get_mem_clock() {
  local f

  f="$CARD_PATH/freq2_input"
  if [ -r "$f" ]; then
    awk '{printf "%.0f MHz", $1/1000000}' "$f" 2>/dev/null
    return
  fi

  f="$CARD_PATH/pp_dpm_mclk"
  if [ -r "$f" ]; then
    awk '/\*/ {gsub("Mhz","",$2); print $2 " MHz"}' "$f" 2>/dev/null
    return
  fi

  echo "Not available"
}

get_gpu_voltage() {
  local hwmon
  hwmon=$(find_hwmon_path)

  if [ -n "$hwmon" ] && [ -r "$hwmon/in0_input" ]; then
    awk '{printf "%.0f mV", $1}' "$hwmon/in0_input" 2>/dev/null
    return
  fi

  if [ -n "$hwmon" ] && [ -r "$hwmon/voltage1_input" ]; then
    awk '{printf "%.0f mV", $1/1000}' "$hwmon/voltage1_input" 2>/dev/null
    return
  fi

  if [ -r "$DEV" ]; then
    awk '
      /OD_VDDC_CURVE:/ {curve=1; next}
      curve && /^[0-9]+:/ {
        gsub(":", "", $1)
        last_freq=$2
        last_mv=$3
      }
      END {
        if (last_mv != "") print last_mv " mV (OD target)"
        else print "Not available"
      }
    ' "$DEV" 2>/dev/null
    return
  fi

  echo "Not available"
}

get_gpu_load() {
  local f

  f="$CARD_PATH/gpu_busy_percent"
  if [ -r "$f" ]; then
    awk '{print $1 " %"}' "$f" 2>/dev/null
    return
  fi

  echo "Not available"
}

get_gpu_power() {
  local hwmon
  hwmon=$(find_hwmon_path)

  if [ -n "$hwmon" ] && [ -r "$hwmon/power1_average" ]; then
    awk '{printf "%.2f W", $1/1000000}' "$hwmon/power1_average" 2>/dev/null
    return
  fi

  if [ -n "$hwmon" ] && [ -r "$hwmon/power1_input" ]; then
    awk '{printf "%.2f W", $1/1000000}' "$hwmon/power1_input" 2>/dev/null
    return
  fi

  echo "Not available"
}

get_gpu_temp() {
  local hwmon
  hwmon=$(find_hwmon_path)

  if [ -n "$hwmon" ] && [ -r "$hwmon/temp1_input" ]; then
    awk '{printf "%.1f °C", $1/1000}' "$hwmon/temp1_input" 2>/dev/null
    return
  fi

  echo "Not available"
}

show_realtime_status() {
  echo "Opening real-time monitor..."
  echo "Press Ctrl+C to return."
  sleep 1

  while true; do
    clear
    echo "========================================"
    echo " AMD BC-250 Real-Time Monitor"
    echo "========================================"
    echo

    if [ -f "$PIDFILE" ]; then
      PID=$(cat "$PIDFILE")
      if kill -0 "$PID" 2>/dev/null; then
        echo "UV loop status : RUNNING (PID $PID)"
      else
        echo "UV loop status : STOPPED (stale PID file)"
      fi
    else
      echo "UV loop status : STOPPED"
    fi

    echo
    echo "[Live values]"
    echo "GPU Clock   : $(get_gpu_clock)"
    echo "Memory Clock: $(get_mem_clock)"
    echo "Voltage     : $(get_gpu_voltage)"
    echo "GPU Load    : $(get_gpu_load)"
    echo "Power       : $(get_gpu_power)"
    echo "Temp        : $(get_gpu_temp)"
    echo
    echo "Refreshing every 1 second..."
    sleep 1
  done
}

show_menu() {
  clear
  cat << 'BANNER'
 ________  ________                  _______  ________  ________            
|\   __  \|\   ____\                /  ___  \|\   ____\|\   __  \           
\ \  \|\ /\ \  \___|  ____________ /__/|_/  /\ \  \___|\ \  \|\  \          
 \ \   __  \ \  \    |\____________\__|//  / /\ \_____  \ \  \\\  \         
  \ \  \|\  \ \  \___\|____________|   /  /_/__\|____|\  \ \  \\\  \        
   \ \_______\ \_______\              |\________\____\_\  \ \_______\       
    \|_______|\|_______|               \|_______|\_________\|_______|       
                                                \|_________|                

 ________  ________  ________   _________  ________  ________  ___          
|\   ____\|\   __  \|\   ___  \|\___   ___\\   __  \|\   __  \|\  \         
\ \  \___|\ \  \|\  \ \  \\ \  \|___ \  \_\ \  \|\  \ \  \|\  \ \  \        
 \ \  \    \ \  \\\  \ \  \\ \  \   \ \  \ \ \   _  _\ \  \\\  \ \  \       
  \ \  \____\ \  \\\  \ \  \\ \  \   \ \  \ \ \  \\  \\ \  \\\  \ \  \____  
   \ \_______\ \_______\ \__\\ \__\   \ \__\ \ \__\\ _\\ \_______\ \_______\
    \|_______|\|_______|\|__| \|__|    \|__|  \|__|\|__|\|_______|\|_______|
                                                                            
BANNER
  echo
  echo " AMD BC-250 Undervolt Control"
  echo
  echo " [1] Gaming mode      (loop 2000 MHz / 925 mV)"
  echo " [2] Balanced mode    (loop 1500 MHz / 810 mV)"
  echo " [3] Power saving     (loop 1000 MHz / 700 mV)"
  echo " [4] Stop undervolt loop only"
  echo " [5] Reset OD table (not guaranteed stock on BC-250)"
  echo " [6] Real-time monitor"
  echo " [0] Exit"
  echo
}

main() {
  require_root
  wait_for_device

  while true; do
    show_menu
    read -rp "Choose an option: " opt
    case "$opt" in
      1)
        gaming_mode
        read -rp "Press Enter to return to menu..."
        ;;
      2)
        balanced_mode
        read -rp "Press Enter to return to menu..."
        ;;
      3)
        power_saving_mode
        read -rp "Press Enter to return to menu..."
        ;;
      4)
        stop_undervolt_loop
        read -rp "Press Enter to return to menu..."
        ;;
      5)
        reset_od_table
        read -rp "Press Enter to return to menu..."
        ;;
      6)
        show_realtime_status
        ;;
      0)
        echo "Exiting..."
        exit 0
        ;;
      *)
        echo "Invalid option."
        sleep 1
        ;;
    esac
  done
}

main
