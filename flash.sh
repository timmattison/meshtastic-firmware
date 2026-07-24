#!/usr/bin/env bash
#
# flash.sh -- first-try-correct flasher for Meshtastic MUI (color-UI) builds.
# (RED skeleton -- intentionally incomplete; see flash.test.sh.)
set -uo pipefail

FLASH_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIO_SH="${FLASH_SH_DIR}/pio.sh"

# WRONG on purpose (red): always claims "not an MUI env".
fh_is_mui_env() { return 1; }

# WRONG on purpose (red): always rejects.
fh_symbols_have_tftsetup() {
  cat >/dev/null 2>&1
  return 1
}

fh_main() {
  set -euo pipefail
  local env="" port="" assume_yes=0
  while [ $# -gt 0 ]; do
    case "$1" in
      -e | --environment) env="${2:-}"; shift 2 ;;
      --port | --upload-port) port="${2:-}"; shift 2 ;;
      -y | --yes) assume_yes=1; shift ;;
      *) shift ;;
    esac
  done
  "${PIO_SH}" run -e "${env}"
  # (red: no erase step)
  if [ -n "${port}" ]; then
    "${PIO_SH}" run -e "${env}" -t upload --upload-port "${port}"
  else
    "${PIO_SH}" run -e "${env}" -t upload
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  fh_main "$@"
fi
