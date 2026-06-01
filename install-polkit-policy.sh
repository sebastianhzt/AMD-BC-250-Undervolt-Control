#!/usr/bin/env bash
#
# Install a Polkit rule for bc250-control-helper.sh.
# The rule uses AUTH_ADMIN_KEEP so Polkit can cache authorization instead of
# asking for the password on every single GUI action.

set -eu

POLICY_ID="org.bc250.control.helper"
POLICY_PATH="/usr/share/polkit-1/actions/${POLICY_ID}.policy"
RULE_PATH="/etc/polkit-1/rules.d/49-bc250-control.rules"
SCRIPT_DIR="$(readlink -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)")"
HELPER_PATH="${BC250_HELPER:-${SCRIPT_DIR}/bc250-control-helper.sh}"

[ -x "$HELPER_PATH" ] || {
  echo "Helper is not executable: $HELPER_PATH" >&2
  echo "Run: chmod +x bc250-control-helper.sh" >&2
  exit 1
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  if command -v pkexec >/dev/null 2>&1; then
    exec pkexec "$0" "$@"
  fi
  echo "Please run with sudo/root:" >&2
  echo "  sudo $0" >&2
  exit 1
fi

mkdir -p "$(dirname "$RULE_PATH")"

cat > "$RULE_PATH" <<EOF
// Installed by BC-250 Control Center.
// Allows Polkit to remember admin authentication for this exact helper path.
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.policykit.exec" &&
        action.lookup("program") == "${HELPER_PATH}") {
        return polkit.Result.AUTH_ADMIN_KEEP;
    }
});
EOF

chmod 0644 "$RULE_PATH"

if [ -d "$(dirname "$POLICY_PATH")" ] && [ -w "$(dirname "$POLICY_PATH")" ]; then
  cat > "$POLICY_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>BC-250 Control Center</vendor>
  <vendor_url>https://github.com/sebastianhzt/AMD-BC-250-Undervolt-Control</vendor_url>
  <action id="${POLICY_ID}">
    <description>Apply AMD BC-250 GPU tuning settings</description>
    <message>Authentication is required to apply BC-250 GPU tuning settings</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">${HELPER_PATH}</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
EOF

  chmod 0644 "$POLICY_PATH"
fi

echo "Installed Polkit rule:"
echo "  $RULE_PATH"
if [ -f "$POLICY_PATH" ]; then
  echo
  echo "Installed Polkit action metadata:"
  echo "  $POLICY_PATH"
fi
echo
echo "Helper path authorized:"
echo "  $HELPER_PATH"
echo
echo "You may need to restart the GUI before testing cached authorization."
