# AMD BC-250 Undervolt Control

Small Bash menu to apply undervolt profiles to the AMD BC-250 GPU under Linux
using the `amdgpu` driver and OverDrive.

Tested on Fedora/Bazzite systems with the BC-250 exposed as `card1`. The tools
default to `card1`, but can target another DRM card such as `card0`.

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
- GTK graphical control panel.
- Three undervolt profiles, each applied in a background loop.
- Stop-loop option that only stops the background writer.
- Reset OverDrive table option, documented as an `amdgpu` reset and not as a
  guaranteed stock restore.
- Real-time GPU monitor integrated in the script.
- Terminal mode is self-contained in `bc250-control.sh`.

## Profiles

```text
Gaming mode      2000 MHz / 925 mV
Balanced mode    1500 MHz / 810 mV
Power saving     1000 MHz / 700 mV
```

Each profile periodically writes to the selected card's OverDrive table. By
default that is:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```

This loop helps fight cases where the BC-250 firmware/SMU overwrites the
voltage/frequency table under load.

## Requirements

- Linux with the `amdgpu` driver.
- AMD BC-250 or compatible card exposed as a Linux DRM card such as `card0` or
  `card1`.
- OverDrive/PP features enabled for `amdgpu`, for example:

```text
amdgpu.ppfeaturemask=0xffffffff
```

You can check your kernel command line with:

```text
cat /proc/cmdline | tr ' ' '\n' | grep amdgpu.ppfeaturemask
```

You also need `bash`, `sudo`, and write access to the selected card's OverDrive
table. By default that is:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```

The default target is `card1`. If your BC-250 is exposed as another DRM card,
use `--card` or `BC250_CARD`.

GUI example:

```text
BC250_CARD=card0 ./bc250-control-gui.py
```

Terminal example:

```text
sudo ./bc250-control.sh --card card0
```

## GUI Dependencies

The graphical interface uses Python and GTK 3 through PyGObject.

Fedora/Bazzite:

```text
sudo rpm-ostree install python3-gobject gtk3 polkit
```

Ubuntu:

```text
sudo apt install python3-gi gir1.2-gtk-3.0 policykit-1
```

Arch Linux:

```text
sudo pacman -S python-gobject gtk3 polkit
```

`polkit`/`pkexec` is used so the GUI can ask for privileges only when it needs
to apply a profile, stop the loop, or reset the OD table. The GUI starts a
privileged helper session on the first privileged action, so the password should
only be requested once while the app remains open.

To avoid typing your password on every single change, install the included
Polkit rule once:

```text
./install-polkit-policy.sh
```

The rule is installed to `/etc/polkit-1/rules.d/49-bc250-control.rules` and uses
`AUTH_ADMIN_KEEP`, so Polkit can also remember your authorization after the
first password prompt. The GUI still shows its own confirmation dialog before
applying a tuning profile.

## Installation

Clone the repository and enter the directory:

```text
git clone https://github.com/sebastianhzt/AMD-BC-250-Undervolt-Control.git
cd AMD-BC-250-Undervolt-Control
```

Make the script executable:

```text
chmod +x bc250-control.sh bc250-control-helper.sh bc250-control-gui.py
chmod +x install-dependencies.sh
chmod +x install-polkit-policy.sh
```

You can optionally copy it somewhere on your `$PATH`:

```text
sudo cp bc250-control.sh /usr/local/sbin/
```

To let the project install the GUI dependencies and optionally install/update
the Polkit rule, run:

```text
./install-dependencies.sh
```

For unattended setup:

```text
./install-dependencies.sh --yes
```

## GUI Usage

Run the graphical control panel:

```text
./bc250-control-gui.py
```

The GUI shows live GPU status, a temperature history graph, quick profile
buttons, custom MHz/mV sliders, a loop interval slider, and controls to stop the
loop or reset the OverDrive table.

The interface language can be changed from the `Language` drop-down. Translation
files live in:

```text
locales/
```

To add another language, copy `locales/en.json` to a new file such as
`locales/fr.json`, translate the values, and update `__language_name`. The GUI
loads every `*.json` file in that folder automatically.

The window adapts between wide, medium, and narrow layouts. Press `F11` for a
fullscreen control-panel view, and press `Esc` to leave fullscreen.

Privileged actions are handled by:

```text
bc250-control-helper.sh
```

The GUI keeps this helper open as a privileged session after the first
successful authentication. Closing the GUI also closes the helper session.

The helper validates requested values before writing to `sysfs`:

```text
SCLK: 1000-2000 MHz
VDDC: 700-1129 mV
Loop interval: 1-60 seconds
```

If your BC-250 is exposed as another DRM card, launch the GUI with:

```text
BC250_CARD=card0 ./bc250-control-gui.py
```

## Terminal Usage

Run the script with sudo:

```text
sudo ./bc250-control.sh
```

If your BC-250 is not `card1`, pass the card explicitly:

```text
sudo ./bc250-control.sh --card card0
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

## GitHub Release Builds

Release artifacts can be generated with:

```text
./packaging/build-release.sh
```

You can set a version manually:

```text
VERSION=1.0.0 ./packaging/build-release.sh
```

The build script creates:

```text
dist/AMD-BC-250-Undervolt-Control-<version>.tar.gz
dist/BC-250-Control-Center.AppDir/
```

If `appimagetool` is installed, it also creates:

```text
dist/BC-250-Control-Center-<version>-<arch>.AppImage
```

The AppImage is a portable launcher for the GTK GUI. It still uses the system
Python GTK/PyGObject runtime, because bundling GTK and PyGObject reliably across
distros is heavier and more fragile than using distro packages. If the runtime
is missing, the AppImage opens the included dependency setup script.

For Polkit compatibility, the AppImage copies `bc250-control-helper.sh` to:

```text
~/.local/share/bc250-control/bc250-control-helper.sh
```

That gives Polkit a stable helper path to authorize, instead of the temporary
mount path used by AppImage.

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
- You are targeting the right DRM card, for example `card1` or `card0`.

List DRM cards with:

```text
ls /sys/class/drm | grep card
```

### Card appears as `card0` instead of `card1`

Launch the GUI or terminal script with the correct card:

```text
BC250_CARD=card0 ./bc250-control-gui.py
sudo ./bc250-control.sh --card card0
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
