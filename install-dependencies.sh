#!/usr/bin/env bash
#
# Install runtime dependencies for BC-250 Control Center and optionally install
# the Polkit rule used by privileged GUI actions.

set -eu

ASSUME_YES=0
INSTALL_POLKIT=1
SCRIPT_DIR="$(readlink -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)")"

usage() {
  cat <<EOF
Usage:
  ./install-dependencies.sh [--yes] [--no-polkit]

Options:
  --yes        Do not ask before installing packages or Polkit rules.
  --no-polkit  Install only distro packages.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    --no-polkit)
      INSTALL_POLKIT=0
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ask() {
  local prompt="$1"
  local answer

  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi

  printf "%s [y/N] " "$prompt"
  read -r answer
  case "$answer" in
    y|Y|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

run_privileged() {
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  elif command -v pkexec >/dev/null 2>&1; then
    pkexec "$@"
  else
    echo "Need sudo or pkexec to run: $*" >&2
    exit 1
  fi
}

gtk_ready() {
  python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk
PY
}

polkit_ready() {
  command -v pkexec >/dev/null 2>&1
}

load_os_release() {
  ID=""
  ID_LIKE=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
}

install_packages() {
  load_os_release

  if command -v rpm-ostree >/dev/null 2>&1 && [ -d /run/ostree-booted ]; then
    echo "Detected rpm-ostree based system."
    echo "Packages: python3-gobject gtk3 polkit"
    ask "Install required packages with rpm-ostree?" || return 0
    run_privileged rpm-ostree install python3-gobject gtk3 polkit
    echo
    echo "rpm-ostree may require a reboot before the new packages are active."
    return 0
  fi

  case " ${ID:-} ${ID_LIKE:-} " in
    *" fedora "*|*" rhel "*)
      echo "Detected Fedora/RHEL-like system."
      echo "Packages: python3-gobject gtk3 polkit"
      ask "Install required packages with dnf?" || return 0
      run_privileged dnf install -y python3-gobject gtk3 polkit
      ;;
    *" ubuntu "*|*" debian "*)
      echo "Detected Debian/Ubuntu-like system."
      echo "Packages: python3-gi gir1.2-gtk-3.0 policykit-1"
      ask "Install required packages with apt?" || return 0
      run_privileged apt-get update
      run_privileged apt-get install -y python3-gi gir1.2-gtk-3.0 policykit-1
      ;;
    *" arch "*)
      echo "Detected Arch-like system."
      echo "Packages: python-gobject gtk3 polkit"
      ask "Install required packages with pacman?" || return 0
      run_privileged pacman -S --needed python-gobject gtk3 polkit
      ;;
    *)
      echo "Could not detect a supported distro automatically."
      echo
      echo "Install these packages manually, then run this script again:"
      echo "  Fedora/Bazzite: python3-gobject gtk3 polkit"
      echo "  Ubuntu/Debian:  python3-gi gir1.2-gtk-3.0 policykit-1"
      echo "  Arch:           python-gobject gtk3 polkit"
      ;;
  esac
}

echo "BC-250 Control Center dependency setup"
echo

if gtk_ready && polkit_ready; then
  echo "GTK/PyGObject and Polkit are already available."
else
  install_packages
fi

echo
if gtk_ready; then
  echo "GTK/PyGObject check: OK"
else
  echo "GTK/PyGObject check: missing"
fi

if polkit_ready; then
  echo "Polkit check: OK"
else
  echo "Polkit check: missing"
fi

if [ "$INSTALL_POLKIT" -eq 1 ]; then
  echo
  if ask "Install/update the BC-250 Polkit rule for cached GUI authorization?"; then
    if [ -x "$SCRIPT_DIR/install-polkit-policy.sh" ]; then
      "$SCRIPT_DIR/install-polkit-policy.sh"
    else
      echo "Missing or non-executable install-polkit-policy.sh in $SCRIPT_DIR" >&2
      exit 1
    fi
  fi
fi

echo
echo "Setup finished."
