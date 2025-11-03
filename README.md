# AMD BC-250 Undervolt Control

Small Bash menu to control undervolt profiles for the AMD BC-250 GPU under Linux (using the `amdgpu` driver and OverDrive).

It uses simple loops that periodically write to:

```text
/sys/class/drm/card1/device/pp_od_clk_voltage
```
to fight against the BC-250 firmware/SMU overwriting the voltage/frequency table under load.

⚠️ Disclaimer
Use at your own risk. Undervolting / overclocking / messing with sysfs can cause system instability, crashes, data loss, or hardware damage.
This script comes with no warranty of any kind.
