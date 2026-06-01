#!/usr/bin/env python3
"""GTK control panel for AMD BC-250 GPU undervolting."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

try:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
except Exception as exc:  # pragma: no cover - only used when GTK is missing.
    print("GTK/PyGObject is required to run the GUI.", file=sys.stderr)
    print("Fedora/Bazzite: sudo rpm-ostree install python3-gobject gtk3", file=sys.stderr)
    print("Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0", file=sys.stderr)
    print("Arch: sudo pacman -S python-gobject gtk3", file=sys.stderr)
    print(f"Import error: {exc}", file=sys.stderr)
    raise SystemExit(1)


APP_DIR = Path(__file__).resolve().parent
HELPER = Path(os.environ.get("BC250_HELPER", APP_DIR / "bc250-control-helper.sh")).expanduser()
CARD = os.environ.get("BC250_CARD", "card1")
CARD_PATH = Path("/sys/class/drm") / CARD / "device"
DEV = CARD_PATH / "pp_od_clk_voltage"
PIDFILE = Path("/run/bc250-uv-loop.pid")
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bc250-control" / "config.json"
LOCALE_DIR = APP_DIR / "locales"
LOOP_PROCESS_MARKER = "bc250-uv-mode-loop"
LOOP_MISS_LIMIT = 3
HELPER_SESSION_READY = "__BC250_READY__"
HELPER_SESSION_DONE_PREFIX = "__BC250_DONE__:"
loop_status_misses = 0
last_loop_status_text = "Stopped"

PROFILES = {
    "Gaming": (2000, 925),
    "Balanced": (1500, 810),
    "Power Saving": (1000, 700),
}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def find_hwmon_path() -> Path | None:
    for path in sorted((CARD_PATH / "hwmon").glob("hwmon*")):
        if path.is_dir():
            return path
    return None


def read_hwmon_value(name: str, divisor: float = 1.0, suffix: str = "") -> str:
    hwmon = find_hwmon_path()
    if hwmon is None:
        return "N/A"

    text = read_text(hwmon / name)
    if text is None:
        return "N/A"

    try:
        value = float(text) / divisor
    except ValueError:
        return "N/A"

    if value.is_integer():
        return f"{int(value)}{suffix}"
    return f"{value:.1f}{suffix}"


def read_hwmon_number(name: str, divisor: float = 1.0) -> float | None:
    hwmon = find_hwmon_path()
    if hwmon is None:
        return None

    text = read_text(hwmon / name)
    if text is None:
        return None

    try:
        return float(text) / divisor
    except ValueError:
        return None


def parse_active_dpm(path: Path) -> str:
    text = read_text(path)
    if not text:
        return "N/A"

    for line in text.splitlines():
        if "*" in line:
            parts = line.replace("*", "").split()
            return parts[1] if len(parts) > 1 else line.replace("*", "").strip()

    return "N/A"


def parse_od_state() -> tuple[str, str]:
    text = read_text(DEV)
    if not text:
        return "N/A", "N/A"

    sclk = "N/A"
    vddc = "N/A"
    section = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("OD_SCLK"):
            section = "sclk"
            continue
        if line.startswith("OD_VDDC"):
            section = "vddc"
            continue
        if line.startswith("OD_RANGE"):
            section = None
            continue
        if line.startswith("0:"):
            parts = line.replace("*", "").split()
            if len(parts) >= 2 and section == "sclk":
                sclk = parts[1]
            elif len(parts) >= 2 and section == "vddc":
                vddc = parts[1]

    return sclk, vddc


def process_exists(pid: int) -> bool:
    if (Path("/proc") / str(pid)).exists():
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def process_has_marker(pid: int, marker: str) -> bool:
    cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        text = cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except PermissionError:
        return True
    except OSError:
        return False
    return marker in text


def find_loop_pid() -> str | None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"[b]c250-uv-mode-loop"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        result = None

    if result is not None:
        for line in result.stdout.splitlines():
            pid = line.strip()
            if pid.isdigit() and int(pid) != os.getpid():
                return pid

    for proc in Path("/proc").glob("[0-9]*"):
        pid = proc.name
        if pid.isdigit() and int(pid) != os.getpid() and process_has_marker(int(pid), LOOP_PROCESS_MARKER):
            return pid

    return None


def read_live_loop_status() -> tuple[bool, str]:
    pid = read_text(PIDFILE) if PIDFILE.exists() else None
    if pid and pid.isdigit():
        numeric_pid = int(pid)
        if process_exists(numeric_pid) and process_has_marker(numeric_pid, LOOP_PROCESS_MARKER):
            return True, f"Running (PID {pid})"
        found_pid = find_loop_pid()
        if found_pid is not None:
            return True, f"Running (PID {found_pid})"
        return False, "Stopped"

    found_pid = find_loop_pid()
    if found_pid is not None:
        return True, f"Running (PID {found_pid})"
    return False, "Stopped"


def loop_status() -> str:
    global last_loop_status_text, loop_status_misses

    is_running, status_text = read_live_loop_status()
    if is_running:
        loop_status_misses = 0
        last_loop_status_text = status_text
        return status_text

    loop_status_misses += 1
    if loop_status_misses < LOOP_MISS_LIMIT and last_loop_status_text.startswith("Running"):
        return last_loop_status_text

    last_loop_status_text = "Stopped"
    return last_loop_status_text


def force_loop_status(status_text: str) -> None:
    global last_loop_status_text, loop_status_misses

    last_loop_status_text = status_text
    loop_status_misses = 0


class MetricCard(Gtk.Box):
    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("metric-card")
        self.set_hexpand(True)

        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("metric-title")
        title_label.set_xalign(0)

        self.value_label = Gtk.Label(label="N/A")
        self.value_label.get_style_context().add_class("metric-value")
        self.value_label.set_xalign(0)
        self.value_label.set_ellipsize(3)

        self.pack_start(title_label, False, False, 0)
        self.pack_start(self.value_label, True, True, 0)

    def set_value(self, value: str) -> None:
        self.value_label.set_text(value)


class Graph(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.max_samples = 240
        self.samples: deque[tuple[float, float]] = deque(maxlen=self.max_samples)
        self.hover_x: float | None = None
        self.hover_y: float | None = None
        self.use_fahrenheit = False
        self.use_12h_time = False
        self.theme = "dark"
        self.set_size_request(-1, 140)
        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("draw", self.on_draw)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_leave)

    @property
    def values(self) -> list[float]:
        return [sample[1] for sample in self.samples]

    def add_value(self, value: float | None) -> None:
        if value is not None:
            self.samples.append((time.time(), value))
        self.queue_draw()

    def on_motion(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        self.hover_x = event.x
        self.hover_y = event.y
        self.queue_draw()
        return True

    def on_leave(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self.hover_x = None
        self.hover_y = None
        self.queue_draw()
        return True

    def set_display_options(self, use_fahrenheit: bool, use_12h_time: bool) -> None:
        self.use_fahrenheit = use_fahrenheit
        self.use_12h_time = use_12h_time
        self.queue_draw()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.queue_draw()

    def format_temp(self, celsius: float) -> str:
        if self.use_fahrenheit:
            return f"{(celsius * 9 / 5) + 32:.1f} °F"
        return f"{celsius:.1f} °C"

    def format_time(self, timestamp: float) -> str:
        fmt = "%I:%M:%S %p" if self.use_12h_time else "%H:%M:%S"
        return time.strftime(fmt, time.localtime(timestamp))

    def on_draw(self, _widget: Gtk.Widget, cr) -> bool:
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 1 or height <= 1:
            return False

        margin_left = 44
        margin_right = 14
        margin_top = 34
        margin_bottom = 24
        plot_x = margin_left
        plot_y = margin_top
        plot_width = max(width - margin_left - margin_right, 1)
        plot_height = max(height - margin_top - margin_bottom, 1)
        minimum = 0.0
        maximum = 100.0
        palette = self.graph_palette()

        values = self.values

        def point(index: int, value: float) -> tuple[float, float]:
            visible_slots = max(self.max_samples - 1, 1)
            first_x = plot_x + plot_width - (len(values) - 1) * (plot_width / visible_slots)
            x = first_x + index * (plot_width / visible_slots)
            clamped = min(max(value, minimum), maximum)
            y = plot_y + plot_height - ((clamped - minimum) / (maximum - minimum) * plot_height)
            return x, y

        if len(palette["outer"]) == 4:
            cr.set_source_rgba(*palette["outer"])
        else:
            cr.set_source_rgb(*palette["outer"])
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Plot background
        if len(palette["plot"]) == 4:
            cr.set_source_rgba(*palette["plot"])
        else:
            cr.set_source_rgb(*palette["plot"])
        cr.rectangle(plot_x, plot_y, plot_width, plot_height)
        cr.fill()

        cr.set_line_width(1.0)
        cr.set_source_rgba(*palette["grid"])
        for temp in range(0, 101, 20):
            y = plot_y + plot_height - ((temp - minimum) / (maximum - minimum) * plot_height)
            cr.move_to(plot_x, y)
            cr.line_to(plot_x + plot_width, y)
            cr.stroke()
            cr.move_to(7, y + 4)
            cr.set_source_rgba(*palette["axis_text"])
            cr.show_text(str(temp))
            cr.set_source_rgba(*palette["grid"])

        cr.set_source_rgba(*palette["grid_soft"])
        for i in range(0, 13):
            x = plot_x + plot_width * i / 12
            cr.move_to(x, plot_y)
            cr.line_to(x, plot_y + plot_height)
            cr.stroke()

        cr.set_source_rgba(*palette["warning"])
        warning_y = plot_y + plot_height - ((85.0 - minimum) / (maximum - minimum) * plot_height)
        cr.move_to(plot_x, warning_y)
        cr.line_to(plot_x + plot_width, warning_y)
        cr.stroke()

        cr.set_source_rgba(*palette["border"])
        cr.rectangle(plot_x, plot_y, plot_width, plot_height)
        cr.stroke()

        if len(values) < 2:
            cr.set_source_rgba(*palette["muted_text"])
            cr.move_to(plot_x + 12, plot_y + 28)
            cr.show_text("Waiting for temperature samples...")
            return False

        first_x, first_y = point(0, values[0])
        last_x, _last_y = point(len(values) - 1, values[-1])

        if first_x > plot_x:
            cr.set_source_rgba(*palette["line_faint"])
            cr.set_line_width(1.0)
            cr.move_to(plot_x, first_y)
            cr.line_to(first_x, first_y)
            cr.stroke()

        # Filled temperature area for real samples only.
        cr.move_to(first_x, first_y)
        for index, value in enumerate(values):
            cr.line_to(*point(index, value))
        cr.line_to(last_x, plot_y + plot_height)
        cr.line_to(first_x, plot_y + plot_height)
        cr.close_path()
        cr.set_source_rgba(*palette["fill"])
        cr.fill()

        # Main trace.
        cr.set_source_rgb(*palette["line"])
        cr.set_line_width(1.35)
        for index, value in enumerate(values):
            x, y = point(index, value)
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        current = values[-1]
        low = min(values)
        high = max(values)
        label = f"GPU0 temp  {self.format_temp(current)}     min {self.format_temp(low)}     max {self.format_temp(high)}"

        cr.set_source_rgb(*palette["line"])
        cr.rectangle(plot_x, 12, 10, 10)
        cr.fill()
        cr.set_source_rgba(*palette["label_text"])
        cr.move_to(plot_x + 18, 22)
        cr.show_text(label)

        if self.hover_x is not None and plot_x <= self.hover_x <= plot_x + plot_width:
            positions = [point(index, value) for index, value in enumerate(values)]
            nearest_index = min(
                range(len(positions)),
                key=lambda index: abs(positions[index][0] - self.hover_x),
            )
            sample_time, sample_temp = self.samples[nearest_index]
            sample_x, sample_y = positions[nearest_index]
            tooltip = f"{self.format_temp(sample_temp)}  {self.format_time(sample_time)}"

            cr.set_source_rgba(*palette["hover_line"])
            cr.set_line_width(1.0)
            cr.move_to(sample_x, plot_y)
            cr.line_to(sample_x, plot_y + plot_height)
            cr.stroke()

            cr.set_source_rgb(*palette["line"])
            cr.arc(sample_x, sample_y, 3.2, 0, 6.28318)
            cr.fill()

            box_width = 128
            box_height = 28
            box_x = sample_x + 10
            if box_x + box_width > plot_x + plot_width:
                box_x = sample_x - box_width - 10
            box_y = max(plot_y + 6, sample_y - box_height - 10)

            cr.set_source_rgba(*palette["tooltip_bg"])
            cr.rectangle(box_x, box_y, box_width, box_height)
            cr.fill()
            cr.set_source_rgba(*palette["tooltip_border"])
            cr.rectangle(box_x, box_y, box_width, box_height)
            cr.stroke()
            cr.set_source_rgba(*palette["tooltip_text"])
            cr.move_to(box_x + 9, box_y + 18)
            cr.show_text(tooltip)

        return False

    def graph_palette(self) -> dict[str, tuple[float, ...]]:
        if self.theme == "light":
            return {
                "outer": (0.96, 0.98, 1.0),
                "plot": (0.985, 0.99, 1.0),
                "grid": (0.10, 0.16, 0.24, 0.16),
                "grid_soft": (0.10, 0.16, 0.24, 0.10),
                "axis_text": (0.12, 0.20, 0.30, 0.88),
                "warning": (0.88, 0.22, 0.16, 0.34),
                "border": (0.18, 0.25, 0.35, 0.46),
                "muted_text": (0.22, 0.30, 0.40, 0.75),
                "line": (0.00, 0.52, 0.45),
                "line_faint": (0.00, 0.52, 0.45, 0.28),
                "fill": (0.00, 0.52, 0.45, 0.12),
                "label_text": (0.08, 0.13, 0.20, 0.96),
                "hover_line": (0.08, 0.13, 0.20, 0.32),
                "tooltip_bg": (1.0, 1.0, 1.0, 0.96),
                "tooltip_border": (0.10, 0.16, 0.24, 0.28),
                "tooltip_text": (0.08, 0.13, 0.20, 0.98),
            }
        if self.theme == "oled":
            return {
                "outer": (0.0, 0.0, 0.0),
                "plot": (0.015, 0.018, 0.022),
                "grid": (0.95, 0.98, 1.0, 0.12),
                "grid_soft": (0.95, 0.98, 1.0, 0.07),
                "axis_text": (0.86, 0.89, 0.94, 0.78),
                "warning": (1.0, 0.35, 0.26, 0.34),
                "border": (0.90, 0.95, 1.0, 0.38),
                "muted_text": (0.86, 0.89, 0.94, 0.72),
                "line": (0.23, 0.93, 0.78),
                "line_faint": (0.23, 0.93, 0.78, 0.22),
                "fill": (0.23, 0.93, 0.78, 0.12),
                "label_text": (0.92, 0.96, 1.0, 0.92),
                "hover_line": (0.92, 0.96, 1.0, 0.32),
                "tooltip_bg": (0.02, 0.025, 0.03, 0.94),
                "tooltip_border": (0.90, 0.95, 1.0, 0.34),
                "tooltip_text": (0.95, 0.98, 1.0, 0.96),
            }
        if self.theme == "custom":
            return {
                "outer": (0.0, 0.0, 0.0, 0.20),
                "plot": (0.02, 0.03, 0.04, 0.34),
                "grid": (0.95, 0.98, 1.0, 0.16),
                "grid_soft": (0.95, 0.98, 1.0, 0.09),
                "axis_text": (0.92, 0.96, 1.0, 0.86),
                "warning": (1.0, 0.35, 0.26, 0.42),
                "border": (0.90, 0.95, 1.0, 0.36),
                "muted_text": (0.92, 0.96, 1.0, 0.74),
                "line": (0.23, 0.93, 0.78),
                "line_faint": (0.23, 0.93, 0.78, 0.25),
                "fill": (0.23, 0.93, 0.78, 0.14),
                "label_text": (0.95, 0.98, 1.0, 0.96),
                "hover_line": (0.95, 0.98, 1.0, 0.34),
                "tooltip_bg": (0.02, 0.03, 0.04, 0.78),
                "tooltip_border": (0.90, 0.95, 1.0, 0.32),
                "tooltip_text": (0.95, 0.98, 1.0, 0.96),
            }
        return {
            "outer": (0.055, 0.060, 0.070),
            "plot": (0.09, 0.10, 0.12),
            "grid": (0.95, 0.98, 1.0, 0.13),
            "grid_soft": (0.95, 0.98, 1.0, 0.08),
            "axis_text": (0.86, 0.89, 0.94, 0.78),
            "warning": (1.0, 0.35, 0.26, 0.34),
            "border": (0.90, 0.95, 1.0, 0.38),
            "muted_text": (0.86, 0.89, 0.94, 0.72),
            "line": (0.23, 0.93, 0.78),
            "line_faint": (0.23, 0.93, 0.78, 0.22),
            "fill": (0.23, 0.93, 0.78, 0.12),
            "label_text": (0.92, 0.96, 1.0, 0.92),
            "hover_line": (0.92, 0.96, 1.0, 0.32),
            "tooltip_bg": (0.05, 0.06, 0.075, 0.92),
            "tooltip_border": (0.90, 0.95, 1.0, 0.34),
            "tooltip_text": (0.95, 0.98, 1.0, 0.96),
        }


class ControlWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="BC-250 Control Center")
        self.set_default_size(1180, 760)
        self.set_size_request(360, 420)
        self.set_border_width(0)

        self.metrics: dict[str, MetricCard] = {}
        self.log_buffer = Gtk.TextBuffer()
        self.metric_grid: Gtk.Grid | None = None
        self.content: Gtk.Box | None = None
        self.left_panel: Gtk.Box | None = None
        self.right_panel: Gtk.Box | None = None
        self.log_scroller: Gtk.ScrolledWindow | None = None
        self.custom_section: Gtk.Box | None = None
        self.custom_grid: Gtk.Grid | None = None
        self.clock_value_entry: Gtk.Entry | None = None
        self.voltage_value_entry: Gtk.Entry | None = None
        self.interval_value_entry: Gtk.Entry | None = None
        self.apply_custom_button: Gtk.Button | None = None
        self.temp_unit_combo: Gtk.ComboBoxText | None = None
        self.time_format_combo: Gtk.ComboBoxText | None = None
        self.theme_combo: Gtk.ComboBoxText | None = None
        self.language_combo: Gtk.ComboBoxText | None = None
        self.root: Gtk.Box | None = None
        self.background_path: Path | None = None
        self.custom_provider: Gtk.CssProvider | None = None
        self.helper_session: subprocess.Popen | None = None
        self.current_theme = "dark"
        self.wallpaper_opacity = 0.78
        self.panel_opacity = 0.78
        self.custom_css_update_id: int | None = None
        self.use_fahrenheit = False
        self.use_12h_time = False
        self.applied_mhz = 1500
        self.applied_mv = 810
        self.applied_interval = 5
        self.is_fullscreen = False
        self.current_layout = ""
        self.loading_settings = False
        self.language = self.read_settings_file().get("language", "en")
        self.translations = self.load_translations(self.language)

        self._build_css()
        self._build_ui()
        self.load_settings()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("size-allocate", self.on_size_allocate)
        self.connect("key-press-event", self.on_key_press)
        self.connect("window-state-event", self.on_window_state)
        self.connect("destroy", self.on_destroy)
        self.connect_after("button-press-event", self.on_window_button_press)
        self.refresh_metrics()
        GLib.timeout_add_seconds(1, self.refresh_metrics)

    def _build_css(self) -> None:
        css = b"""
        window {
          background: #15171b;
          color: #f2f4f8;
          font-family: Inter, Cantarell, Sans;
        }
        .topbar {
          padding: 16px 20px;
          background: #1d2026;
          border-bottom: 1px solid #30343d;
        }
        .topbar-row {
          border: none;
        }
        .title {
          font-size: 24px;
          font-weight: 800;
        }
        .subtitle {
          color: #aeb6c2;
        }
        .header-controls {
          border: none;
        }
        .header-select {
          min-width: 132px;
        }
        .select-title {
          color: #9da7b5;
          font-size: 11px;
          font-weight: 700;
        }
        combobox button {
          min-height: 32px;
          padding-left: 10px;
          padding-right: 10px;
        }
        .section {
          padding: 14px;
          background: #1d2026;
          border: 1px solid #30343d;
          border-radius: 8px;
        }
        .fullscreen-panel .section {
          padding: 22px;
        }
        .section-title {
          font-size: 15px;
          font-weight: 800;
          color: #f2f4f8;
        }
        .control-title {
          color: #f2f4f8;
          font-size: 12px;
          font-weight: 700;
        }
        .control-value {
          color: #dce3ee;
          font-size: 12px;
          font-weight: 700;
        }
        .value-entry {
          min-height: 28px;
          padding-left: 6px;
          padding-right: 6px;
          border: 1px solid transparent;
          border-radius: 6px;
          background: transparent;
          color: #dce3ee;
          box-shadow: none;
        }
        .value-entry:hover {
          background: rgba(255, 255, 255, 0.07);
          border-color: rgba(255, 255, 255, 0.12);
        }
        .value-entry:focus {
          background: rgba(255, 255, 255, 0.09);
          border-color: #2d9c7f;
        }
        .metric-card {
          padding: 12px;
          background: #252932;
          border: 1px solid #383d48;
          border-radius: 8px;
          min-width: 130px;
        }
        .metric-title {
          color: #9da7b5;
          font-size: 12px;
          font-weight: 700;
        }
        .metric-value {
          color: #ffffff;
          font-size: 22px;
          font-weight: 800;
        }
        .fullscreen-panel .metric-value {
          font-size: 30px;
        }
        .compact .metric-value {
          font-size: 19px;
        }
        button {
          min-height: 30px;
          border-radius: 7px;
          border: 1px solid #3a4250;
          background: #2c3440;
          color: #f2f4f8;
          font-weight: 700;
        }
        button:hover {
          background: #35404d;
        }
        .primary-button {
          background: #24725f;
          border-color: #319076;
        }
        .danger-button {
          background: #713238;
          border-color: #9a444d;
        }
        scale trough {
          min-height: 7px;
          border-radius: 6px;
          background: #343946;
        }
        scale highlight {
          border-radius: 6px;
          background: #2d9c7f;
        }
        scale slider {
          min-width: 18px;
          min-height: 18px;
          border-radius: 10px;
          background: #e8fff8;
        }
        textview {
          background: #000000;
          color: #d9dee7;
          border-radius: 8px;
          padding: 8px;
        }
        textview text {
          background: #000000;
          color: #d9dee7;
        }
        .log-view {
          background: #000000;
        }
        .log-view text {
          background: #000000;
        }
        .theme-light {
          background: #eef1f5;
          color: #16202d;
        }
        .theme-light .topbar {
          background: #f8fafc;
          border-bottom-color: #d1d9e6;
        }
        .theme-light .title,
        .theme-light .section-title,
        .theme-light .control-title {
          color: #16202d;
        }
        .theme-light .subtitle,
        .theme-light .select-title,
        .theme-light .metric-title {
          color: #5f6d7e;
        }
        .theme-light .section {
          background: #ffffff;
          border-color: #d3dbe7;
        }
        .theme-light .metric-card {
          background: #f1f5f9;
          border-color: #d7dfeb;
        }
        .theme-light .metric-value,
        .theme-light .control-value {
          color: #16202d;
        }
        .theme-light button,
        .theme-light combobox button {
          background: #e7edf5;
          border-color: #c6d0de;
          color: #16202d;
        }
        .theme-light button:hover {
          background: #dbe4ef;
        }
        .theme-light scale trough {
          background: #c9d2df;
        }
        .theme-light .value-entry {
          color: #16202d;
        }
        .theme-light textview,
        .theme-light textview text,
        .theme-light .log-view,
        .theme-light .log-view text {
          background: #ffffff;
          color: #16202d;
        }
        .theme-oled {
          background: #000000;
          color: #f7f9fc;
        }
        .theme-oled .topbar {
          background: #000000;
          border-bottom-color: #1e2630;
        }
        .theme-oled .section {
          background: #050608;
          border-color: #242b35;
        }
        .theme-oled .metric-card {
          background: #0b0d10;
          border-color: #2a3340;
        }
        .theme-oled button,
        .theme-oled combobox button {
          background: #101722;
          border-color: #2b3747;
        }
        .theme-oled button:hover {
          background: #172232;
        }
        .theme-oled scale trough {
          background: #242b35;
        }
        .theme-oled textview,
        .theme-oled textview text,
        .theme-oled .log-view,
        .theme-oled .log-view text {
          background: #000000;
          color: #e7edf5;
        }
        .theme-custom {
          color: #f7f9fc;
        }
        .theme-custom .topbar {
          background: rgba(0, 0, 0, 0.70);
          border-bottom-color: rgba(255, 255, 255, 0.16);
        }
        .theme-custom .section {
          background: rgba(8, 10, 14, 0.78);
          border-color: rgba(255, 255, 255, 0.18);
        }
        .theme-custom .metric-card {
          background: rgba(18, 22, 30, 0.74);
          border-color: rgba(255, 255, 255, 0.16);
        }
        .theme-custom .title,
        .theme-custom .section-title,
        .theme-custom .control-title,
        .theme-custom .metric-value,
        .theme-custom .control-value {
          color: #ffffff;
        }
        .theme-custom .subtitle,
        .theme-custom .select-title,
        .theme-custom .metric-title {
          color: #c8d0dc;
        }
        .theme-custom button,
        .theme-custom combobox button {
          background: rgba(10, 16, 24, 0.46);
          border-color: rgba(255, 255, 255, 0.28);
          color: #f7f9fc;
          box-shadow: none;
        }
        .theme-custom button:hover {
          background: rgba(22, 32, 46, 0.58);
        }
        .theme-custom .primary-button {
          background: rgba(36, 130, 106, 0.62);
          border-color: rgba(80, 210, 175, 0.55);
        }
        .theme-custom .danger-button {
          background: rgba(120, 45, 54, 0.62);
          border-color: rgba(220, 94, 104, 0.55);
        }
        .theme-custom scale trough {
          background: rgba(10, 16, 24, 0.40);
          border: 1px solid rgba(255, 255, 255, 0.24);
        }
        .theme-custom scale highlight {
          background: rgba(54, 230, 190, 0.48);
        }
        .theme-custom scale slider {
          background: rgba(238, 255, 250, 0.58);
          border: 1px solid rgba(255, 255, 255, 0.45);
        }
        .theme-custom .value-entry {
          color: #ffffff;
          background: transparent;
        }
        .theme-custom textview,
        .theme-custom textview text,
        .theme-custom .log-view,
        .theme-custom .log-view text {
          background: rgba(0, 0, 0, 0.38);
          color: #eef4ff;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.root = root
        root.set_can_focus(True)
        self.focus_sink = root
        self.add(root)

        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        topbar.get_style_context().add_class("topbar")
        topbar.get_style_context().add_class("topbar-row")

        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label=self.t("app_title"))
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        title.set_line_wrap(True)
        subtitle = Gtk.Label(label=self.t("subtitle").format(card=CARD))
        subtitle.get_style_context().add_class("subtitle")
        subtitle.set_xalign(0)
        subtitle.set_line_wrap(True)
        heading.pack_start(title, False, False, 0)
        heading.pack_start(subtitle, False, False, 0)
        topbar.pack_start(heading, True, True, 0)

        header_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_controls.get_style_context().add_class("header-controls")
        temp_selector, self.temp_unit_combo = self._header_select(
            self.t("temperature_unit"),
            [("c", self.t("celsius")), ("f", self.t("fahrenheit"))],
        )
        self.temp_unit_combo.set_active_id("f" if self.use_fahrenheit else "c")
        self.temp_unit_combo.connect("changed", self.on_temp_unit_changed)
        time_selector, self.time_format_combo = self._header_select(
            self.t("time_format"),
            [("24", self.t("time_24")), ("12", self.t("time_12"))],
        )
        self.time_format_combo.set_active_id("12" if self.use_12h_time else "24")
        self.time_format_combo.connect("changed", self.on_time_format_changed)
        theme_selector, self.theme_combo = self._header_select(
            self.t("theme"),
            [("dark", self.t("theme_dark")), ("light", self.t("theme_light")), ("oled", self.t("theme_oled")), ("custom", self.t("theme_custom"))],
        )
        self.theme_combo.set_active_id(self.current_theme)
        self.theme_combo.connect("changed", self.on_theme_changed)
        language_selector, self.language_combo = self._header_select(
            self.t("language"),
            self.available_languages(),
        )
        self.language_combo.set_active_id(self.language)
        self.language_combo.connect("changed", self.on_language_changed)
        customize_selector, customize_button = self._header_button(self.t("appearance"), self.t("customize"))
        customize_button.connect("clicked", self.on_customize_clicked)
        header_controls.pack_start(temp_selector, False, False, 0)
        header_controls.pack_start(time_selector, False, False, 0)
        header_controls.pack_start(theme_selector, False, False, 0)
        header_controls.pack_start(language_selector, False, False, 0)
        header_controls.pack_start(customize_selector, False, False, 0)
        topbar.pack_start(header_controls, False, False, 0)
        root.pack_start(topbar, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_overlay_scrolling(True)
        root.pack_start(scroller, True, True, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.content.set_border_width(18)
        self.content.set_hexpand(True)
        self.content.set_vexpand(True)
        scroller.add(self.content)

        self.left_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.right_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.left_panel.set_hexpand(True)
        self.left_panel.set_vexpand(True)
        self.content.pack_start(self.left_panel, True, True, 0)
        self.content.pack_start(self.right_panel, False, False, 0)
        self.right_panel.set_size_request(330, -1)

        status_section = self._section(self.t("live_gpu_status"))
        self.metric_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        status_section.pack_start(self.metric_grid, True, True, 0)
        metric_items = [
            ("Core", self.t("metric_core")),
            ("OD Clock", self.t("metric_od_clock")),
            ("Voltage", self.t("metric_voltage")),
            ("Load", self.t("metric_load")),
            ("Power", self.t("metric_power")),
            ("Temp", self.t("metric_temp")),
            ("Memory", self.t("metric_memory")),
            ("Loop", self.t("metric_loop")),
        ]
        for index, (name, label) in enumerate(metric_items):
            card = MetricCard(label)
            self.metrics[name] = card
            card.set_hexpand(True)
            self.metric_grid.attach(card, index % 4, index // 4, 1, 1)
        self.left_panel.pack_start(status_section, False, False, 0)

        graph_section = self._section(self.t("temperature_history"))
        self.graph = Graph()
        graph_section.pack_start(self.graph, True, True, 0)
        self.left_panel.pack_start(graph_section, False, False, 0)

        log_section = self._section(self.t("log"))
        self.log_scroller = Gtk.ScrolledWindow()
        self.log_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.log_scroller.set_overlay_scrolling(True)
        self.log_view = Gtk.TextView(buffer=self.log_buffer)
        self.log_view.get_style_context().add_class("log-view")
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_scroller.add(self.log_view)
        self.log_scroller.set_size_request(-1, 160)
        log_section.pack_start(self.log_scroller, True, True, 0)
        self.left_panel.pack_start(log_section, False, False, 0)

        profiles = self._section(self.t("profiles"))
        profile_labels = {
            "Gaming": self.t("profile_gaming"),
            "Balanced": self.t("profile_balanced"),
            "Power Saving": self.t("profile_power_saving"),
        }
        for label, (mhz, mv) in PROFILES.items():
            button = Gtk.Button(label=f"{profile_labels[label]}  {mhz} MHz / {mv} mV")
            button.connect("clicked", self.on_profile_clicked, mhz, mv)
            profiles.pack_start(button, False, False, 0)
        self.right_panel.pack_start(profiles, False, False, 0)

        self.custom_section = self._section(self.t("custom_tuning"))
        self.custom_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.clock_scale = self._scale(1000, 2000, 50, 1500)
        self.voltage_scale = self._scale(700, 1129, 5, 810)
        self.interval_scale = self._scale(1, 30, 1, 5)
        self.clock_value_entry = self._compact_scale_row(self.custom_grid, 0, self.t("core_clock"), "MHz", self.clock_scale, 1000, 2000)
        self.voltage_value_entry = self._compact_scale_row(self.custom_grid, 1, self.t("voltage"), "mV", self.voltage_scale, 700, 1129)
        self.interval_value_entry = self._compact_scale_row(self.custom_grid, 2, self.t("loop_interval"), "s", self.interval_scale, 1, 30)
        self.custom_section.pack_start(self.custom_grid, False, False, 0)

        self.apply_custom_button = Gtk.Button(label=self.t("apply_custom_profile"))
        self.apply_custom_button.get_style_context().add_class("primary-button")
        self.apply_custom_button.connect("clicked", self.on_apply_custom_clicked)
        revert_custom = Gtk.Button(label=self.t("revert_changes"))
        revert_custom.connect("clicked", lambda *_: self.restore_applied_controls())
        custom_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        custom_buttons.pack_start(revert_custom, True, True, 0)
        custom_buttons.pack_start(self.apply_custom_button, True, True, 0)
        self.custom_section.pack_start(custom_buttons, False, False, 0)
        self.right_panel.pack_start(self.custom_section, False, False, 0)

        controls = self._section(self.t("controls"))
        stop_button = Gtk.Button(label=self.t("stop_loop"))
        stop_button.connect("clicked", lambda *_: self.run_helper(["stop"]))
        reset_button = Gtk.Button(label=self.t("reset_od_table"))
        reset_button.get_style_context().add_class("danger-button")
        reset_button.connect("clicked", self.on_reset_clicked)
        controls.pack_start(stop_button, False, False, 0)
        controls.pack_start(reset_button, False, False, 0)
        self.right_panel.pack_start(controls, False, False, 0)

    def on_size_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        self.apply_responsive_layout(allocation.width)

    def on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = Gdk.keyval_name(event.keyval)
        if key == "F11":
            self.toggle_fullscreen()
            return True
        if key == "Escape" and self.is_fullscreen:
            self.unfullscreen()
            return True
        return False

    def on_window_state(self, _widget: Gtk.Widget, event: Gdk.EventWindowState) -> bool:
        self.is_fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        return False

    def on_window_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        event_widget = Gtk.get_event_widget(event)
        if self.is_descendant_of(event_widget, self.apply_custom_button):
            return False

        if not any(
            self.is_descendant_of(event_widget, entry)
            for entry in self.numeric_entries()
            if entry is not None
        ):
            self.reset_numeric_entries_to_sliders()
            if hasattr(self, "focus_sink"):
                self.focus_sink.grab_focus()
        return False

    def is_descendant_of(self, widget: Gtk.Widget | None, parent: Gtk.Widget | None) -> bool:
        while widget is not None:
            if widget is parent:
                return True
            widget = widget.get_parent()
        return False

    def toggle_fullscreen(self) -> None:
        if self.is_fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()

    def on_temp_unit_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.use_fahrenheit = combo.get_active_id() == "f"
        self.graph.set_display_options(self.use_fahrenheit, self.use_12h_time)
        self.refresh_metrics()
        if not self.loading_settings:
            self.save_settings()

    def on_time_format_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.use_12h_time = combo.get_active_id() == "12"
        self.graph.set_display_options(self.use_fahrenheit, self.use_12h_time)
        if not self.loading_settings:
            self.save_settings()

    def on_theme_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.apply_theme(combo.get_active_id() or "dark")
        if not self.loading_settings:
            self.save_settings()

    def on_language_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self.loading_settings:
            return
        language = combo.get_active_id() or "en"
        if language == self.language:
            return
        self.language = language
        self.translations = self.load_translations(language)
        self.rebuild_ui()
        self.save_settings()

    def apply_theme(self, theme: str) -> None:
        if self.root is None:
            return

        style = self.root.get_style_context()
        for class_name in ("theme-dark", "theme-light", "theme-oled", "theme-custom"):
            style.remove_class(class_name)
        if theme not in {"dark", "light", "oled", "custom"}:
            theme = "dark"
        self.current_theme = theme
        style.add_class(f"theme-{theme}")
        if hasattr(self, "graph"):
            self.graph.set_theme(theme)
        self.update_custom_css()

    def rebuild_ui(self) -> None:
        if self.root is not None:
            self.remove(self.root)
        self.metrics = {}
        self.metric_grid = None
        self.content = None
        self.left_panel = None
        self.right_panel = None
        self.log_scroller = None
        self.custom_section = None
        self.custom_grid = None
        self.clock_value_entry = None
        self.voltage_value_entry = None
        self.interval_value_entry = None
        self.apply_custom_button = None
        self.temp_unit_combo = None
        self.time_format_combo = None
        self.theme_combo = None
        self.language_combo = None
        self._build_ui()
        self.apply_theme(self.current_theme)
        self.restore_applied_controls()
        self.refresh_metrics()
        self.show_all()

    def t(self, key: str) -> str:
        return str(self.translations.get(key, key))

    def available_languages(self) -> list[tuple[str, str]]:
        languages: list[tuple[str, str]] = []
        for path in sorted(LOCALE_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            languages.append((path.stem, str(data.get("__language_name", path.stem))))
        if not languages:
            languages = [("en", "English")]
        return languages

    def load_translations(self, language: str) -> dict[str, str]:
        fallback = self.load_translation_file("en")
        selected = self.load_translation_file(language)
        fallback.update(selected)
        return fallback

    def load_translation_file(self, language: str) -> dict[str, str]:
        path = LOCALE_DIR / f"{language}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def load_settings(self) -> None:
        self.loading_settings = True
        data = self.read_settings_file()

        self.wallpaper_opacity = self.clamp_float(data.get("wallpaper_opacity", self.wallpaper_opacity), 0.25, 1.0)
        self.panel_opacity = self.clamp_float(data.get("panel_opacity", self.panel_opacity), 0.35, 1.0)

        background = data.get("background_path")
        if isinstance(background, str) and background:
            path = Path(background).expanduser()
            if path.exists():
                self.background_path = path

        temp_unit = data.get("temperature_unit", "c")
        time_format = data.get("time_format", "24")
        theme = data.get("theme", "dark")

        if self.temp_unit_combo is not None:
            self.temp_unit_combo.set_active_id("f" if temp_unit == "f" else "c")
        if self.time_format_combo is not None:
            self.time_format_combo.set_active_id("12" if time_format == "12" else "24")
        if self.theme_combo is not None:
            self.theme_combo.set_active_id(theme if theme in {"dark", "light", "oled", "custom"} else "dark")
        if self.language_combo is not None:
            self.language_combo.set_active_id(self.language)

        self.use_fahrenheit = temp_unit == "f"
        self.use_12h_time = time_format == "12"
        self.graph.set_display_options(self.use_fahrenheit, self.use_12h_time)
        self.apply_theme(theme if theme in {"dark", "light", "oled", "custom"} else "dark")
        self.loading_settings = False

    def read_settings_file(self) -> dict:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def save_settings(self) -> None:
        data = {
            "theme": self.current_theme,
            "language": self.language,
            "temperature_unit": "f" if self.use_fahrenheit else "c",
            "time_format": "12" if self.use_12h_time else "24",
            "wallpaper_opacity": self.wallpaper_opacity,
            "panel_opacity": self.panel_opacity,
            "background_path": str(self.background_path) if self.background_path is not None else "",
        }

        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            self.append_log(f"Could not save settings: {exc}")

    def clamp_float(self, value, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return minimum
        return max(min(number, maximum), minimum)

    def on_customize_clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.Dialog(title=self.t("customize_appearance"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(self.t("close"), Gtk.ResponseType.CLOSE)

        area = dialog.get_content_area()
        area.set_border_width(16)
        area.set_spacing(12)

        title = Gtk.Label(label=self.t("background_image"))
        title.get_style_context().add_class("section-title")
        title.set_xalign(0)
        detail = Gtk.Label(
            label=self.t("custom_appearance_detail")
        )
        detail.set_line_wrap(True)
        detail.set_xalign(0)

        choose_button = Gtk.Button(label=self.t("choose_image"))
        choose_button.connect("clicked", self.on_choose_background_clicked, dialog)
        clear_button = Gtk.Button(label=self.t("clear_background"))
        clear_button.connect("clicked", lambda *_: self.clear_background_image())

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.pack_start(choose_button, True, True, 0)
        actions.pack_start(clear_button, True, True, 0)

        wallpaper_control = self._dialog_percent_control(
            self.t("wallpaper_opacity"),
            self.t("wallpaper_opacity_detail"),
            25,
            100,
            int(self.wallpaper_opacity * 100),
            self.on_wallpaper_opacity_changed,
        )
        panel_control = self._dialog_percent_control(
            self.t("panel_opacity"),
            self.t("panel_opacity_detail"),
            35,
            100,
            int(self.panel_opacity * 100),
            self.on_panel_opacity_changed,
        )

        area.pack_start(title, False, False, 0)
        area.pack_start(detail, False, False, 0)
        area.pack_start(actions, False, False, 0)
        area.pack_start(wallpaper_control, False, False, 0)
        area.pack_start(panel_control, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_choose_background_clicked(self, _button: Gtk.Button, parent_dialog: Gtk.Dialog) -> None:
        chooser = Gtk.FileChooserDialog(
            title=self.t("choose_background_image"),
            transient_for=parent_dialog,
            action=Gtk.FileChooserAction.OPEN,
        )
        chooser.add_button(self.t("cancel"), Gtk.ResponseType.CANCEL)
        chooser.add_button(self.t("open"), Gtk.ResponseType.OK)

        image_filter = Gtk.FileFilter()
        image_filter.set_name(self.t("images"))
        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            image_filter.add_pattern(pattern)
            image_filter.add_pattern(pattern.upper())
        chooser.add_filter(image_filter)

        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            filename = chooser.get_filename()
            if filename:
                self.apply_background_image(Path(filename))
        chooser.destroy()

    def apply_background_image(self, path: Path) -> None:
        if self.root is None:
            return

        self.background_path = path
        if self.theme_combo is not None:
            self.theme_combo.set_active_id("custom")
        else:
            self.apply_theme("custom")
        self.update_custom_css()
        self.save_settings()
        self.append_log(self.t("background_image_set").format(path=path))

    def clear_background_image(self) -> None:
        self.background_path = None
        self.update_custom_css()
        self.save_settings()
        self.append_log(self.t("background_image_cleared"))

    def update_custom_css(self) -> None:
        if self.custom_provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self.custom_provider)
            self.custom_provider = None

        if self.current_theme != "custom":
            return

        background_css = "background: #050608;"
        if self.background_path is not None:
            uri = self.background_path.resolve().as_uri()
            background_css = (
                f'background-image: linear-gradient(rgba(0, 0, 0, {1 - self.wallpaper_opacity:.2f}), '
                f'rgba(0, 0, 0, {1 - self.wallpaper_opacity:.2f})), url("{uri}");'
                "background-size: cover;"
                "background-position: center;"
                "background-repeat: no-repeat;"
            )

        panel = self.panel_opacity
        card = min(panel + 0.05, 0.96)
        control = max(min(panel - 0.22, 0.72), 0.28)
        primary = max(min(panel - 0.12, 0.78), 0.42)
        log_alpha = max(min(panel - 0.28, 0.70), 0.22)
        slider_trough = max(min(panel - 0.35, 0.48), 0.18)
        slider_fill = max(min(panel - 0.25, 0.62), 0.28)
        slider_knob = max(min(panel - 0.18, 0.72), 0.36)
        css = f"""
        window {{
          {background_css}
        }}
        .theme-custom .topbar {{
          background: rgba(0, 0, 0, {max(panel - 0.08, 0.25):.2f});
        }}
        .theme-custom .section {{
          background: rgba(8, 10, 14, {panel:.2f});
        }}
        .theme-custom .metric-card {{
          background: rgba(18, 22, 30, {card:.2f});
        }}
        .theme-custom button,
        .theme-custom combobox button {{
          background: rgba(10, 16, 24, {control:.2f});
        }}
        .theme-custom button:hover {{
          background: rgba(22, 32, 46, {min(control + 0.12, 0.85):.2f});
        }}
        .theme-custom .primary-button {{
          background: rgba(36, 130, 106, {primary:.2f});
        }}
        .theme-custom .danger-button {{
          background: rgba(120, 45, 54, {primary:.2f});
        }}
        .theme-custom textview,
        .theme-custom textview text,
        .theme-custom .log-view,
        .theme-custom .log-view text {{
          background: rgba(0, 0, 0, {log_alpha:.2f});
        }}
        .theme-custom scale trough {{
          background: rgba(10, 16, 24, {slider_trough:.2f});
        }}
        .theme-custom scale highlight {{
          background: rgba(54, 230, 190, {slider_fill:.2f});
        }}
        .theme-custom scale slider {{
          background: rgba(238, 255, 250, {slider_knob:.2f});
        }}
        """.encode("utf-8")

        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        self.custom_provider = provider

    def on_wallpaper_opacity_changed(self, spin: Gtk.SpinButton) -> None:
        self.wallpaper_opacity = spin.get_value() / 100.0
        self.schedule_custom_css_update()
        self.save_settings()

    def on_panel_opacity_changed(self, spin: Gtk.SpinButton) -> None:
        self.panel_opacity = spin.get_value() / 100.0
        self.schedule_custom_css_update()
        self.save_settings()

    def schedule_custom_css_update(self) -> None:
        if self.custom_css_update_id is not None:
            GLib.source_remove(self.custom_css_update_id)
        self.custom_css_update_id = GLib.timeout_add(120, self.run_scheduled_custom_css_update)

    def run_scheduled_custom_css_update(self) -> bool:
        self.custom_css_update_id = None
        self.update_custom_css()
        return False

    def _dialog_percent_control(
        self,
        title: str,
        description: str,
        minimum: int,
        maximum: int,
        value: int,
        callback,
    ) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.get_style_context().add_class("control-title")

        desc = Gtk.Label(label=description)
        desc.set_xalign(0)
        desc.set_line_wrap(True)
        desc.get_style_context().add_class("subtitle")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 1)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        spin = Gtk.SpinButton()
        spin.set_range(minimum, maximum)
        spin.set_increments(1, 5)
        spin.set_digits(0)
        spin.set_numeric(True)
        spin.set_value(value)
        spin.set_width_chars(4)
        suffix = Gtk.Label(label="%")

        def sync_from_scale(widget: Gtk.Scale) -> None:
            spin.set_value(widget.get_value())

        def sync_from_spin(widget: Gtk.SpinButton) -> None:
            scale.set_value(widget.get_value())
            callback(widget)

        scale.set_value(value)
        scale.connect("value-changed", sync_from_scale)
        spin.connect("value-changed", sync_from_spin)
        row.pack_start(scale, True, True, 0)
        row.pack_start(spin, False, False, 0)
        row.pack_start(suffix, False, False, 0)

        box.pack_start(label, False, False, 0)
        box.pack_start(desc, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def format_temp(self, celsius: float) -> str:
        if self.use_fahrenheit:
            return f"{(celsius * 9 / 5) + 32:.1f} °F"
        return f"{celsius:.1f} °C"

    def apply_responsive_layout(self, width: int) -> None:
        if self.content is None or self.left_panel is None or self.right_panel is None:
            return

        mode = "wide"
        if width >= 1500:
            mode = "fullscreen"
        elif width < 760:
            mode = "narrow"
        elif width < 1080:
            mode = "medium"

        if mode != self.current_layout:
            self.current_layout = mode
            if mode == "fullscreen":
                self.content.set_orientation(Gtk.Orientation.HORIZONTAL)
                self.content.set_spacing(28)
                self.right_panel.set_size_request(430, -1)
                self.content.set_border_width(26)
                self.graph.set_size_request(-1, 210)
                if self.log_scroller is not None:
                    self.log_scroller.set_size_request(-1, 220)
                metric_columns = 4
            elif mode == "wide":
                self.content.set_orientation(Gtk.Orientation.HORIZONTAL)
                self.content.set_spacing(12)
                self.right_panel.set_size_request(340, -1)
                self.content.set_border_width(12)
                self.graph.set_size_request(-1, 128)
                if self.log_scroller is not None:
                    self.log_scroller.set_size_request(-1, 92)
                metric_columns = 4
            elif mode == "medium":
                self.content.set_orientation(Gtk.Orientation.VERTICAL)
                self.content.set_spacing(18)
                self.right_panel.set_size_request(-1, -1)
                self.content.set_border_width(16)
                self.graph.set_size_request(-1, 150)
                if self.log_scroller is not None:
                    self.log_scroller.set_size_request(-1, 150)
                metric_columns = 4
            else:
                self.content.set_orientation(Gtk.Orientation.VERTICAL)
                self.content.set_spacing(12)
                self.right_panel.set_size_request(-1, -1)
                self.content.set_border_width(10)
                self.graph.set_size_request(-1, 130)
                if self.log_scroller is not None:
                    self.log_scroller.set_size_request(-1, 120)
                metric_columns = 2

            self.reflow_metric_grid(metric_columns)

        style = self.get_style_context()
        if mode == "narrow":
            style.add_class("compact")
        else:
            style.remove_class("compact")
        if mode == "fullscreen":
            style.add_class("fullscreen-panel")
        else:
            style.remove_class("fullscreen-panel")

    def reflow_metric_grid(self, columns: int) -> None:
        if self.metric_grid is None:
            return

        for child in list(self.metric_grid.get_children()):
            self.metric_grid.remove(child)

        for index, name in enumerate(["Core", "OD Clock", "Voltage", "Load", "Power", "Temp", "Memory", "Loop"]):
            self.metric_grid.attach(self.metrics[name], index % columns, index // columns, 1, 1)

        self.metric_grid.show_all()

    def _section(self, title: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("section")
        box.set_hexpand(True)
        label = Gtk.Label(label=title)
        label.get_style_context().add_class("section-title")
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        return box

    def _header_select(self, title: str, items: list[tuple[str, str]]) -> tuple[Gtk.Box, Gtk.ComboBoxText]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0)
        title_label.get_style_context().add_class("select-title")
        combo = Gtk.ComboBoxText()
        combo.get_style_context().add_class("header-select")
        for item_id, label in items:
            combo.append(item_id, label)
        combo.set_active(0)

        box.pack_start(title_label, False, False, 0)
        box.pack_start(combo, False, False, 0)
        return box, combo

    def _header_button(self, title: str, value: str) -> tuple[Gtk.Box, Gtk.Button]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0)
        title_label.get_style_context().add_class("select-title")
        button = Gtk.Button(label=value)
        button.get_style_context().add_class("header-select")
        box.pack_start(title_label, False, False, 0)
        box.pack_start(button, False, False, 0)
        return box, button

    def _scale(self, minimum: int, maximum: int, step: int, value: int) -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
        scale.set_value(value)
        scale.set_digits(0)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.connect("scroll-event", self.on_scale_scroll)
        return scale

    def on_scale_scroll(self, _scale: Gtk.Scale, _event: Gdk.EventScroll) -> bool:
        return True

    def _scale_row(self, label: str, unit: str, scale: Gtk.Scale) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=label)
        name.set_xalign(0)
        value = Gtk.Label()
        value.set_xalign(1)
        value.set_width_chars(10)

        def update_value(widget: Gtk.Scale) -> None:
            value.set_text(f"{int(widget.get_value())} {unit}")

        scale.connect("value-changed", update_value)
        update_value(scale)

        header.pack_start(name, True, True, 0)
        header.pack_start(value, False, False, 0)
        row.pack_start(header, False, False, 0)
        row.pack_start(scale, False, False, 0)
        return row

    def _compact_scale_row(
        self,
        grid: Gtk.Grid,
        row: int,
        label: str,
        unit: str,
        scale: Gtk.Scale,
        minimum: int,
        maximum: int,
    ) -> Gtk.Entry:
        name = Gtk.Label(label=label)
        name.get_style_context().add_class("control-title")
        name.set_xalign(0)
        value = Gtk.Entry()
        value.get_style_context().add_class("value-entry")
        value.set_width_chars(5)
        value.set_max_length(len(str(maximum)))
        value.set_alignment(1.0)
        value.set_input_purpose(Gtk.InputPurpose.DIGITS)
        value.set_text(str(int(scale.get_value())))
        value._bc250_editing = False

        def update_value(widget: Gtk.Scale) -> None:
            if getattr(value, "_bc250_editing", False):
                return
            text = str(int(widget.get_value()))
            if value.get_text() != text:
                value.set_text(text)

        scale.connect("value-changed", update_value)
        value.connect("insert-text", self.on_numeric_insert_text)
        value.connect("changed", self.on_numeric_entry_changed, scale, minimum, maximum)
        value.connect("focus-in-event", self.on_numeric_entry_focus_in)
        value.connect("focus-out-event", self.on_numeric_entry_focus_out, scale, minimum, maximum)
        value.connect("activate", self.on_numeric_entry_activate, scale, minimum, maximum)
        value.connect("button-press-event", self.on_numeric_entry_pressed)
        update_value(scale)
        scale.set_size_request(185, -1)

        value_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        value_box.pack_start(value, False, False, 0)
        unit_label = Gtk.Label(label=unit)
        unit_label.get_style_context().add_class("control-value")
        value_box.pack_start(unit_label, False, False, 0)

        grid.attach(name, 0, row, 1, 1)
        grid.attach(scale, 1, row, 1, 1)
        grid.attach(value_box, 2, row, 1, 1)
        return value

    def on_numeric_insert_text(self, entry: Gtk.Entry, text: str, length: int, position: int) -> None:
        del length
        if not text.isdigit():
            entry.stop_emission_by_name("insert-text")
            return

        current = entry.get_text()
        proposed = current[:position] + text + current[position:]
        if not proposed.isdigit():
            entry.stop_emission_by_name("insert-text")

    def on_numeric_entry_changed(
        self,
        entry: Gtk.Entry,
        scale: Gtk.Scale,
        minimum: int,
        maximum: int,
    ) -> None:
        del scale, minimum, maximum
        text = entry.get_text()
        if text and not text.isdigit():
            entry.set_text("".join(ch for ch in text if ch.isdigit()))

    def on_numeric_entry_focus_in(self, entry: Gtk.Entry, _event: Gdk.EventFocus) -> bool:
        entry._bc250_editing = True
        GLib.idle_add(self.clear_numeric_entry_text, entry)
        return False

    def on_numeric_entry_focus_out(
        self,
        entry: Gtk.Entry,
        scale: Gtk.Scale,
        minimum: int,
        maximum: int,
    ) -> bool:
        del scale, minimum, maximum
        GLib.timeout_add(120, self.restore_if_numeric_abandoned, entry)
        return False

    def restore_if_numeric_abandoned(self, entry: Gtk.Entry) -> bool:
        focus = self.get_focus()
        if focus is self.apply_custom_button:
            return False
        if focus not in {self.clock_value_entry, self.voltage_value_entry, self.interval_value_entry}:
            entry._bc250_editing = False
            self.reset_numeric_entries_to_sliders()
        return False

    def on_numeric_entry_activate(
        self,
        entry: Gtk.Entry,
        scale: Gtk.Scale,
        minimum: int,
        maximum: int,
    ) -> None:
        entry._bc250_editing = False
        self.commit_numeric_entry(entry, scale, minimum, maximum)

    def on_numeric_entry_pressed(self, entry: Gtk.Entry, _event: Gdk.EventButton) -> bool:
        entry._bc250_editing = True
        GLib.idle_add(self.clear_numeric_entry_text, entry)
        return False

    def clear_numeric_entry_text(self, entry: Gtk.Entry) -> bool:
        if getattr(entry, "_bc250_editing", False):
            entry.set_text("")
        return False

    def numeric_entries(self) -> tuple[Gtk.Entry | None, Gtk.Entry | None, Gtk.Entry | None]:
        return self.clock_value_entry, self.voltage_value_entry, self.interval_value_entry

    def numeric_pairs(self) -> tuple[tuple[Gtk.Entry | None, Gtk.Scale], ...]:
        return (
            (self.clock_value_entry, self.clock_scale),
            (self.voltage_value_entry, self.voltage_scale),
            (self.interval_value_entry, self.interval_scale),
        )

    def reset_numeric_entries_to_sliders(self) -> None:
        for entry, scale in self.numeric_pairs():
            if entry is None:
                continue
            entry._bc250_editing = False
            entry.set_text(str(int(scale.get_value())))
            entry.select_region(0, 0)

    def commit_numeric_entries(self) -> None:
        entries = (
            (self.clock_value_entry, self.clock_scale, 1000, 2000),
            (self.voltage_value_entry, self.voltage_scale, 700, 1129),
            (self.interval_value_entry, self.interval_scale, 1, 30),
        )
        for entry, scale, minimum, maximum in entries:
            if entry is None:
                continue
            entry._bc250_editing = False
            self.commit_numeric_entry(entry, scale, minimum, maximum)

    def commit_numeric_entry(self, entry: Gtk.Entry, scale: Gtk.Scale, minimum: int, maximum: int) -> None:
        text = entry.get_text()
        if not text:
            value = int(scale.get_value())
        else:
            value = max(min(int(text), maximum), minimum)
        entry.set_text(str(value))
        scale.set_value(value)

    def on_destroy(self, *_args) -> None:
        self.close_helper_session()

    def close_helper_session(self) -> None:
        process = self.helper_session
        self.helper_session = None
        if process is None or process.poll() is not None:
            return

        try:
            if process.stdin is not None:
                process.stdin.write("quit\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()

    def privileged_command(self, args: list[str]) -> list[str] | None:
        command = [str(HELPER), "--card", CARD, *args]
        if os.geteuid() == 0:
            return command
        if shutil.which("pkexec"):
            return ["pkexec", *command]
        if shutil.which("sudo"):
            return ["sudo", *command]
        return None

    def start_helper_session(self) -> bool:
        if self.helper_session is not None and self.helper_session.poll() is None:
            return True

        if os.geteuid() != 0 and not shutil.which("pkexec"):
            return False

        command = self.privileged_command(["session"])
        if command is None:
            self.append_log("No pkexec or sudo found for privileged actions.")
            return False

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.append_log(f"Failed to start helper session: {exc}")
            return False

        startup_lines: list[str] = []
        if process.stdout is None:
            self.append_log("Failed to start helper session: no output pipe")
            process.terminate()
            return False

        while True:
            line = process.stdout.readline()
            if line == "":
                output = "".join(startup_lines).strip()
                if output:
                    self.append_log(output)
                self.append_log("Privileged helper session did not start.")
                process.wait(timeout=1)
                return False

            text = line.rstrip()
            if text == HELPER_SESSION_READY:
                self.helper_session = process
                self.append_log("Privileged helper session unlocked.")
                return True
            startup_lines.append(line)

    def run_helper_session_command(self, args: list[str]) -> tuple[int, str] | None:
        if not self.start_helper_session() or self.helper_session is None:
            return None

        process = self.helper_session
        if process.stdin is None or process.stdout is None:
            self.close_helper_session()
            return None

        try:
            process.stdin.write(" ".join(args) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.append_log(f"Helper session closed: {exc}")
            self.close_helper_session()
            return None

        output_lines: list[str] = []
        while True:
            line = process.stdout.readline()
            if line == "":
                self.append_log("Helper session ended unexpectedly.")
                self.close_helper_session()
                return None

            text = line.rstrip()
            if text.startswith(HELPER_SESSION_DONE_PREFIX):
                try:
                    status = int(text.removeprefix(HELPER_SESSION_DONE_PREFIX))
                except ValueError:
                    status = 1
                return status, "".join(output_lines).strip()

            output_lines.append(line)

    def append_log(self, message: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, message.rstrip() + "\n")
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

    def run_helper(self, args: list[str]) -> None:
        if not HELPER.exists():
            self.append_log(f"Helper not found: {HELPER}")
            return

        session_result = self.run_helper_session_command(args)
        if session_result is not None:
            returncode, output = session_result
        else:
            command = self.privileged_command(args)
            if command is None:
                self.append_log("No pkexec or sudo found for privileged actions.")
                return

            try:
                result = subprocess.run(command, check=False, capture_output=True, text=True)
            except OSError as exc:
                self.append_log(f"Failed to run helper: {exc}")
                return

            returncode = result.returncode
            output = (result.stdout + result.stderr).strip()

        if output:
            self.append_log(output)
        if returncode != 0:
            self.append_log(f"Command failed with exit code {returncode}")
            return

        action = args[0] if args else ""
        if action == "apply":
            pid = read_text(PIDFILE)
            force_loop_status(f"Running (PID {pid})" if pid and pid.isdigit() else "Running")
        elif action in {"stop", "reset"}:
            force_loop_status("Stopped")

        self.refresh_metrics()

    def on_profile_clicked(self, _button: Gtk.Button, mhz: int, mv: int) -> None:
        if not self.validate_tuning_request(mhz, mv):
            self.restore_applied_controls()
            return

        if not self.confirm_apply(mhz, mv, int(self.interval_scale.get_value())):
            self.restore_applied_controls()
            return

        self.clock_scale.set_value(mhz)
        self.voltage_scale.set_value(mv)
        interval = int(self.interval_scale.get_value())
        self.mark_applied(mhz, mv, interval)
        self.run_helper(["apply", str(mhz), str(mv), str(interval)])

    def on_apply_custom_clicked(self, _button: Gtk.Button) -> None:
        self.commit_numeric_entries()
        mhz = int(self.clock_scale.get_value())
        mv = int(self.voltage_scale.get_value())
        interval = int(self.interval_scale.get_value())
        if not self.validate_tuning_request(mhz, mv):
            self.restore_applied_controls()
            return

        if not self.confirm_apply(mhz, mv, interval):
            self.restore_applied_controls()
            return

        self.mark_applied(mhz, mv, interval)
        self.run_helper(["apply", str(mhz), str(mv), str(interval)])

    def mark_applied(self, mhz: int, mv: int, interval: int) -> None:
        self.applied_mhz = mhz
        self.applied_mv = mv
        self.applied_interval = interval

    def restore_applied_controls(self) -> None:
        self.clock_scale.set_value(self.applied_mhz)
        self.voltage_scale.set_value(self.applied_mv)
        self.interval_scale.set_value(self.applied_interval)

    def validate_tuning_request(self, mhz: int, mv: int) -> bool:
        if mhz >= 1900 and mv < 850:
            self.show_alert(
                self.t("unsafe_tuning_blocked"),
                self.t("unsafe_tuning_detail").format(mhz=mhz, mv=mv),
                "dialog-warning-symbolic",
            )
            return False

        if mhz >= 1900 and mv < 900:
            return self.confirm_risky_voltage(mhz, mv)

        return True

    def confirm_risky_voltage(self, mhz: int, mv: int) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=self.t("low_voltage_title"),
        )
        dialog.format_secondary_text(
            self.t("low_voltage_detail").format(mhz=mhz, mv=mv)
        )
        dialog.add_button(self.t("continue_anyway"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def show_alert(self, title: str, detail: str, icon_name: str) -> None:
        dialog = Gtk.Dialog(title=title, transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.set_default_size(460, -1)
        dialog.set_resizable(False)
        dialog.add_button(self.t("accept"), Gtk.ResponseType.OK)

        area = dialog.get_content_area()
        area.set_border_width(14)
        area.set_spacing(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("section-title")
        title_label.set_xalign(0)
        header.pack_start(icon, False, False, 0)
        header.pack_start(title_label, True, True, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)

        detail_label = Gtk.Label(label=detail)
        detail_label.set_line_wrap(True)
        detail_label.set_max_width_chars(58)
        detail_label.set_xalign(0)

        area.pack_start(header, False, False, 0)
        area.pack_start(separator, False, False, 0)
        area.pack_start(detail_label, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def confirm_apply(self, mhz: int, mv: int, interval: int) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text=self.t("apply_profile_title"),
        )
        dialog.format_secondary_text(
            self.t("apply_profile_detail").format(mhz=mhz, mv=mv, interval=interval)
        )
        dialog.add_button(self.t("apply"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def on_reset_clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=self.t("reset_od_title"),
        )
        dialog.format_secondary_text(
            self.t("reset_od_detail")
        )
        dialog.add_button(self.t("reset_od_table"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            self.run_helper(["reset"])

    def refresh_metrics(self) -> bool:
        od_clock, od_voltage = parse_od_state()

        gpu_clock = read_text(CARD_PATH / "freq1_input")
        if gpu_clock and gpu_clock.isdigit():
            core = f"{int(gpu_clock) // 1000000} MHz"
        else:
            core = parse_active_dpm(CARD_PATH / "pp_dpm_sclk")

        temp_value = read_hwmon_number("temp1_input", 1000.0)
        temp_text = self.format_temp(temp_value) if temp_value is not None else "N/A"
        power_text = read_hwmon_value("power1_average", 1000000.0, " W")
        voltage_text = read_hwmon_value("in0_input", 1.0, " mV")
        load_text = read_text(CARD_PATH / "gpu_busy_percent")
        load = f"{load_text} %" if load_text and load_text.isdigit() else "N/A"

        self.metrics["Core"].set_value(core)
        self.metrics["OD Clock"].set_value(od_clock)
        self.metrics["Voltage"].set_value(voltage_text if voltage_text != "N/A" else od_voltage)
        self.metrics["Load"].set_value(load)
        self.metrics["Power"].set_value(power_text)
        self.metrics["Temp"].set_value(temp_text)
        self.metrics["Memory"].set_value(parse_active_dpm(CARD_PATH / "pp_dpm_mclk"))
        self.metrics["Loop"].set_value(loop_status())

        self.graph.add_value(temp_value)

        return True


def main() -> int:
    window = ControlWindow()
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
