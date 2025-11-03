# AMD BC-250 Undervolt Control


# Tested on Fedora 43


Small Bash menu to control undervolt profiles for the AMD BC-250 GPU under Linux (using the `amdgpu` driver and OverDrive).

It uses simple loops that periodically write to:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```
to fight against the BC-250 firmware/SMU overwriting the voltage/frequency table under load.

⚠️ Disclaimer

Use at your own risk. Undervolting / overclocking / messing with sysfs can cause system instability, crashes, data loss, or hardware damage.

This script comes with no warranty of any kind.

Features

ASCII-art TUI menu

Three undervolt profiles, all applied in a loop:

Gaming mode – 2000 MHz / 900 mV
Balanced mode – 1500 MHz / 810 mV
Power saving mode – 1000 MHz / 700 mV

Restore stock option (stop loops + reset OverDrive table).
Self-contained: only one script (bc250-control.sh).


Requirements

Linux with the amdgpu driver.

An AMD BC-250 (or compatible) card exposed as card1:
```text
ls /sys/class/drm | grep card
```
# should show something like: card0  card1


OverDrive/PP features enabled for amdgpu, e.g. kernel parameter:
```text
amdgpu.ppfeaturemask=0xffffffff
```

You can add that to your GRUB/boot loader and check with:
```text
cat /proc/cmdline | tr ' ' '\n' | grep amdgpu.ppfeaturemask
```

bash, sudo, and access to /sys/class/drm/card1/device/pp_od_clk_voltage.

If your BC-250 is not card1, edit the DEV=... line at the top of the script.


Installation

Clone this repository and make the script executable:
```text
git clone https://github.com/sebastianhzt/AMD-BC-250-Undervolt-Control.git
cd bc250-undervolt-control
```
```text
chmod +x bc250-control.sh
```

You can optionally copy it somewhere on your $PATH:
```text
sudo cp bc250-control.sh /usr/local/sbin/
```

Usage

Run the script with sudo:
```text
sudo ./bc250-control.sh
```
# or, if copied to /usr/local/sbin:
```text
sudo bc250-control.sh
```

You will see a menu like:
```text
 AMD BC-250 Undervolt Control

 [1] Gaming mode      (loop 2000 MHz / 900 mV)
 [2] Balanced mode    (loop 1500 MHz / 810 mV)
 [3] Power saving     (loop 1000 MHz / 700 mV)
 [4] Restore stock OD table (no loop)
 [0] Exit
```
Modes

Gaming mode
Starts a background loop that writes vc 0 2000 900 + c every 5 seconds.
Good for full load: stable FPS, significantly lower power and temperature compared to stock.

Balanced mode
Starts a loop at 1500 MHz / 810 mV.
Useful for lighter gaming or compute with lower power draw.

Power saving mode
Starts a loop at 1000 MHz / 700 mV.
Good for desktop / idle / light tasks.

Restore stock
Stops any running loop and writes:
```text
echo r > /sys/class/drm/card1/device/pp_od_clk_voltage
```

to reset the OverDrive table to factory values.

Only one loop is active at a time. Switching modes automatically stops the previous loop and starts the new one.


Monitoring

You can monitor what’s actually happening using amdgpu_pm_info:
```text
sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
```
```text
watch -n1 'sudo cat /sys/kernel/debug/dri/1/amdgpu_pm_info | egrep "SCLK|VDDC|GPU Load|GPU Temperature"'
```

Typical expectations:

Gaming mode:

SCLK ≈ 2000 MHz
VDDC around 0.9 V
Much lower temps and power than stock (e.g. 130–140 W vs ~170 W)

Power saving mode:

SCLK ≈ 1000 MHz
VDDC ≈ 0.7 V
Very low temps/power at desktop.

Troubleshooting

Option 2/3 “don’t work” or always look like gaming mode
You probably have an old loop or systemd service still running (from a previous setup) that keeps writing 2000 / 900 in the background.
Make sure to stop/disable any bc250-* systemd services and kill old loops:
```text
sudo pkill -f bc250-uv-mode-loop 2>/dev/null || true
sudo rm -f /run/bc250-uv-loop.pid
```

pp_od_clk_voltage doesn’t exist
Check that:

You’re using the amdgpu driver.

```amdgpu.ppfeaturemask=0xffffffff``` (or similar) is applied.

The card really is card1 (or adjust DEV= in the script).

Card appears as card0 instead of card1
Edit the script:
```text
DEV="/sys/class/drm/card0/device/pp_od_clk_voltage"
```
# Safety notes

This script was designed and tested specifically for AMD BC-250 “mining” cards under Linux.

Other GPUs / BIOSes may expose different OverDrive behavior or ranges.

Always monitor:

Temperatures
Stability (no crashes, no artifacts)
dmesg for amdgpu GPU reset messages.

If you hit instability:

Use Gaming mode first (2000 / 900 is usually safe).

Avoid pushing voltage too low (e.g. 2000 / 880 mV proved unstable on some BC-250 units).


