# AMD BC-250 Undervolt Control

Small Bash menu to apply undervolt profiles to the AMD BC-250 GPU under Linux
using the `amdgpu` driver and OverDrive.

Tested on Fedora/Bazzite systems with the BC-250 exposed as `card1`.

## Warning

Use at your own risk. Undervolting, overclocking, and writing directly to
`sysfs` can cause instability, crashes, data loss, overheating, or hardware
damage.

This script comes with no warranty of any kind.

The BC-250 can behave differently from normal desktop GPUs. On some cards,
resetting the OverDrive table with `echo r` does not fully restore the original
dynamic stock boost behavior. It may keep the last manual SCLK/VDDC point, such
as `1000 MHz`, `1500 MHz`, or another value previously applied.

## Features

- ASCII-art TUI menu.
- Three undervolt profiles, each applied in a background loop.
- Stop-loop option that only stops the background writer.
- Reset OverDrive table option, documented as an `amdgpu` reset and not as a
  guaranteed stock restore.
- Real-time GPU monitor integrated in the script.
- Self-contained: one script, `bc250-control.sh`.

## Profiles

```text
Gaming mode      2000 MHz / 925 mV
Balanced mode    1500 MHz / 810 mV
Power saving     1000 MHz / 700 mV
```

Each profile periodically writes to:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```

This loop helps fight cases where the BC-250 firmware/SMU overwrites the
voltage/frequency table under load.

## Requirements

- Linux with the `amdgpu` driver.
- AMD BC-250 or compatible card exposed as `card1`.
- OverDrive/PP features enabled for `amdgpu`, for example:

```text
amdgpu.ppfeaturemask=0xffffffff
```

You can check your kernel command line with:

```text
cat /proc/cmdline | tr ' ' '\n' | grep amdgpu.ppfeaturemask
```

You also need `bash`, `sudo`, and write access to:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```

If your BC-250 is not `card1`, edit these variables at the top of the script:

```bash
DEV="/sys/class/drm/card1/device/pp_od_clk_voltage"
CARD_PATH="/sys/class/drm/card1/device"
DEBUG_PM_INFO="/sys/kernel/debug/dri/1/amdgpu_pm_info"
```

## Installation

Clone the repository and enter the directory:

```text
git clone https://github.com/sebastianhzt/AMD-BC-250-Undervolt-Control.git
cd AMD-BC-250-Undervolt-Control
```

Make the script executable:

```text
chmod +x bc250-control.sh
```

You can optionally copy it somewhere on your `$PATH`:

```text
sudo cp bc250-control.sh /usr/local/sbin/
```

## Usage

Run the script with sudo:

```text
sudo ./bc250-control.sh
```

If the file is not executable yet, you can run it directly with Bash:

```text
sudo bash ./bc250-control.sh
```

If copied to `/usr/local/sbin`:

```text
sudo bc250-control.sh
```

The menu looks like this:

```text
AMD BC-250 Undervolt Control

[1] Gaming mode      (loop 2000 MHz / 925 mV)
[2] Balanced mode    (loop 1500 MHz / 810 mV)
[3] Power saving     (loop 1000 MHz / 700 mV)
[4] Stop undervolt loop only
[5] Reset OD table (not guaranteed stock on BC-250)
[6] Real-time monitor
[0] Exit
```

## Menu Options

### Gaming mode

Starts a background loop that writes:

```text
vc 0 2000 925
c
```

every 5 seconds.

### Balanced mode

Starts a background loop at `1500 MHz / 810 mV`.

### Power saving mode

Starts a background loop at `1000 MHz / 700 mV`.

### Stop undervolt loop only

Stops the background undervolt loop and removes the PID file. It does not reset
the OverDrive table and does not write new clock or voltage values.

Use this when you only want to stop the script from continuing to write values.

### Reset OD table

Stops the loop, writes:

```text
echo r > /sys/class/drm/card1/device/pp_od_clk_voltage
```

and asks `amdgpu` to return to automatic DPM selection:

```text
echo auto > /sys/class/drm/card1/device/power_dpm_force_performance_level
```

Important: on some BC-250 cards, this does not fully restore the original stock
boost behavior. It can leave the card at the last manual SCLK/VDDC point.

If you need to test true stock behavior, stop the loop, remove any OverDrive
kernel parameter such as `amdgpu.ppfeaturemask=...`, then perform a full power
off and cold boot before running the script again.

### Real-time monitor

Displays live GPU statistics directly inside the script, refreshing every
second:

- GPU core clock
- Memory clock
- Voltage
- GPU load
- Power consumption
- Temperature
- Current undervolt loop status

Press `Ctrl+C` to exit the monitor and return to the main menu.

## Monitoring

You can monitor what is happening using `amdgpu_pm_info`:

```text
sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
watch -n1 'sudo cat /sys/kernel/debug/dri/1/amdgpu_pm_info | egrep "SCLK|VDDC|GPU Load|GPU Temperature"'
```

You can also inspect the public `sysfs` values:

```text
cat /sys/class/drm/card1/device/pp_od_clk_voltage
cat /sys/class/drm/card1/device/pp_dpm_sclk
cat /sys/class/drm/card1/device/power_dpm_force_performance_level
```

## Troubleshooting

### Option 2 or 3 does not work, or the card always returns to another profile

You may have an old loop or service still writing values in the background.
Stop old loops and remove the PID file:

```text
sudo pkill -f bc250-uv-mode-loop 2>/dev/null || true
sudo rm -f /run/bc250-uv-loop.pid
```

### Reset OD table does not restore stock boost

This is known to happen on some BC-250 systems. The `amdgpu` reset command may
keep the last manual point instead of rebuilding the original dynamic boost
behavior.

For true stock testing:

1. Stop the undervolt loop.
2. Remove or temporarily disable `amdgpu.ppfeaturemask=...`.
3. Power off the system completely.
4. Wait 30-60 seconds.
5. Boot again and check `pp_od_clk_voltage` before running this script.

### `pp_od_clk_voltage` does not exist

Check that:

- You are using the `amdgpu` driver.
- OverDrive/PP features are enabled.
- The card really is `card1`.

List DRM cards with:

```text
ls /sys/class/drm | grep card
```

### Card appears as `card0` instead of `card1`

Edit the script variables:

```bash
DEV="/sys/class/drm/card0/device/pp_od_clk_voltage"
CARD_PATH="/sys/class/drm/card0/device"
DEBUG_PM_INFO="/sys/kernel/debug/dri/0/amdgpu_pm_info"
```

## Safety Notes

This script was designed for AMD BC-250 mining cards under Linux.

Other GPUs, BIOS versions, and kernel versions may expose different OverDrive
behavior or different safe voltage ranges.

Always monitor:

- Temperatures
- Power draw
- Stability, crashes, or artifacts
- `dmesg` for `amdgpu` GPU reset messages

If the GPU becomes unstable or overheats, stop the load immediately and return
to a known stable profile.
