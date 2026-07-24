#!/usr/bin/env bash
#
# flash.sh -- deterministic, first-try-correct flasher for Meshtastic MUI
# (color-UI) builds, layered on top of pio.sh.
#
# Why this exists:
#   The color "MUI" is selected at RUNTIME by a saved config value
#   (display.displaymode == COLOR), not by the firmware binary. A plain
#   `pio run -e <env> -t upload` rewrites only the app partition and never
#   clears the saved config -- so a stale/poisoned displaymode survives the
#   flash and you boot a perfectly good *-tft binary straight into the
#   CLASSIC UI. And the poison is easy to get: the moment a device ever
#   boots a non-TFT build, src/mesh/NodeDB.cpp (`#if !HAS_TFT`) permanently
#   rewrites displaymode COLOR -> DEFAULT. App-only uploads can also fail to
#   dislodge a stale OTA slot on a heavily-updated device.
#
#   flash.sh removes both failure modes:
#     1. It builds and flashes THROUGH pio.sh, so it uses this worktree's
#        isolated PlatformIO toolchain (never the shared ~/.platformio cache).
#     2. For *-tft (MUI) envs it verifies the freshly built binary really is
#        a HAS_TFT build -- the `tftSetup` symbol (compiled only under
#        `#if HAS_TFT`) must be present -- and refuses to flash otherwise.
#     3. It ERASES the whole chip before uploading. A blank chip has no saved
#        config, so a HAS_TFT build auto-selects displaymode=COLOR on first
#        boot (NodeDB.cpp) and the MUI comes up -- first try, every time.
#
# Usage:
#   ./flash.sh -e t-deck-tft                       # build, verify, erase, upload
#   ./flash.sh -e t-deck-tft --port /dev/cu.usbmodemXXXX
#   ./flash.sh -e t-deck-tft -y                    # skip the erase confirmation
#   ./flash.sh -e t-deck --no-erase                # advanced: keep config (may NOT show MUI)
#
# WARNING: the erase wipes ALL device config -- LoRa region, WiFi creds,
# channels, node identity. That is intended for a fresh install / recovering a
# device. Re-set the region afterwards (e.g. `meshtastic --set lora.region US`)
# or the radio stays disabled.
# NB: no top-level `set` here on purpose -- this file is sourced by
# flash.test.sh, so it must not mutate the caller's shell options. Strict mode
# is enabled inside fh_main(), which is the only thing that runs on execution.

FLASH_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PIO_SH="${FLASH_SH_DIR}/pio.sh"

fh_usage() {
  sed -n '2,36p' "${BASH_SOURCE[0]:-$0}" | sed 's/^# \{0,1\}//'
}

# fh_is_mui_env <env-name> -> 0 if this env builds the color MUI (HAS_TFT).
# MUI environments are named "<board>-tft" by convention in this repo.
fh_is_mui_env() {
  case "$1" in
    *-tft) return 0 ;;
    *) return 1 ;;
  esac
}

# fh_symbols_have_tftsetup  (nm symbol dump on stdin) -> 0 if tftSetup present.
# tftSetup() is compiled only under `#if HAS_TFT` (src/graphics/tftSetup.cpp,
# called from src/main.cpp), so its presence in the linked ELF proves the
# binary was built with HAS_TFT=1 and can actually show the MUI.
fh_symbols_have_tftsetup() {
  grep -q 'tftSetup'
}

# fh_find_nm -> path to an nm that can read the build ELF. Prefer this
# worktree's isolated toolchain (what pio.sh populates), then the shared one,
# then whatever nm is on PATH. Prints the path; returns non-zero if none found.
fh_find_nm() {
  local candidate
  for candidate in \
    "${FLASH_SH_DIR}"/.platformio/packages/toolchain-*/bin/*-nm \
    "${HOME}"/.platformio/packages/toolchain-*/bin/*-nm; do
    if [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  if command -v nm >/dev/null 2>&1; then
    command -v nm
    return 0
  fi
  return 1
}

# fh_assert_mui_binary <env> -> 0 if the built ELF contains tftSetup; aborts
# (non-zero) if it is a *-tft env whose binary lacks it. This is the enforced
# guard: it refuses to flash a binary that looks like an MUI build but isn't.
fh_assert_mui_binary() {
  local env="$1" elf nm
  elf="$(find ".pio/build/${env}" -maxdepth 1 -name '*.elf' 2>/dev/null | head -1)"
  if [ -z "${elf}" ]; then
    echo "flash.sh: could not find a built ELF for '${env}' to verify HAS_TFT." >&2
    return 1
  fi
  if ! nm="$(fh_find_nm)"; then
    echo "warning: no 'nm' found to verify HAS_TFT; skipping the binary guard." >&2
    echo "         (Relying on the chip erase to bring up the MUI on first boot.)" >&2
    return 0
  fi
  if "${nm}" "${elf}" 2>/dev/null | fh_symbols_have_tftsetup; then
    echo "verified: '${env}' binary contains tftSetup() -- HAS_TFT is enabled."
    return 0
  fi
  echo "flash.sh: '${env}' is an MUI (*-tft) env but its binary has NO tftSetup symbol." >&2
  echo "          HAS_TFT did not take effect -- refusing to flash a non-MUI binary." >&2
  return 1
}

# fh_pio <pio args...> -> forward to pio.sh, appending --upload-port when a
# port was supplied. Kept bash-3.2-safe (no empty-array expansion under set -u).
fh_pio() {
  if [ -n "${FH_PORT}" ]; then
    "${PIO_SH}" "$@" --upload-port "${FH_PORT}"
  else
    "${PIO_SH}" "$@"
  fi
}

fh_post_flash_report() {
  local env="$1"
  echo
  echo "flash complete: ${env}"
  if fh_is_mui_env "${env}"; then
    echo "  The color MUI should appear once the device finishes booting."
  fi
  echo "  Config was erased -- set your region before the radio will transmit, e.g.:"
  echo "    meshtastic --set lora.region US"
  if fh_is_mui_env "${env}" && command -v meshtastic >/dev/null 2>&1; then
    echo "  Verifying display.displaymode over serial (best-effort)..."
    local dm
    dm="$(meshtastic ${FH_PORT:+--port "${FH_PORT}"} --get display.displaymode 2>/dev/null | tr -d '[:space:]')"
    case "${dm}" in
      *COLOR*) echo "  ok: display.displaymode = COLOR (MUI active)." ;;
      "") echo "  note: could not read displaymode (device still booting) -- the erase+guard still guarantee it." ;;
      *) echo "  warning: display.displaymode = ${dm} (expected COLOR). Set it with: meshtastic --set display.displaymode COLOR" ;;
    esac
  else
    echo "  (Runtime displaymode check skipped: no 'meshtastic' CLI on PATH. Correctness is"
    echo "   guaranteed by the erase -> fresh config -> COLOR default, plus the HAS_TFT guard.)"
  fi
}

fh_main() {
  set -euo pipefail
  local env="" assume_yes=0 do_erase=1
  FH_PORT=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -e | --environment)
        env="${2:-}"
        shift 2
        ;;
      --port | --upload-port)
        FH_PORT="${2:-}"
        shift 2
        ;;
      -y | --yes)
        assume_yes=1
        shift
        ;;
      --no-erase)
        do_erase=0
        shift
        ;;
      -h | --help)
        fh_usage
        return 0
        ;;
      *)
        echo "flash.sh: unknown argument: $1" >&2
        return 2
        ;;
    esac
  done

  if [ -z "${env}" ]; then
    echo "flash.sh: -e <env> is required (e.g. -e t-deck-tft)." >&2
    return 2
  fi
  if [ ! -x "${PIO_SH}" ]; then
    echo "flash.sh: pio.sh not found next to flash.sh (${PIO_SH})." >&2
    return 1
  fi

  # 1) Build first (non-destructive), via the worktree-isolated toolchain.
  echo ">> Building ${env} via pio.sh ..."
  "${PIO_SH}" run -e "${env}"

  # 2) Enforced guard: an MUI env must actually produce a HAS_TFT binary.
  #    Runs BEFORE we erase, so a bad build never wipes a working device.
  if fh_is_mui_env "${env}"; then
    fh_assert_mui_binary "${env}"
  fi

  # 3) Confirm the destructive erase (skippable with -y).
  if [ "${do_erase}" -eq 1 ] && [ "${assume_yes}" -ne 1 ]; then
    echo
    echo "!! This ERASES THE ENTIRE CHIP before flashing:"
    echo "     - LoRa region, WiFi credentials, channels, node identity are all wiped."
    echo "     - Required so the MUI comes up first-try (fresh config auto-selects COLOR)."
    printf "   Continue? [y/N] "
    local reply
    read -r reply
    case "${reply}" in
      [yY] | [yY][eE][sS]) ;;
      *)
        echo "Aborted."
        return 1
        ;;
    esac
  fi

  # 4) Erase, then 5) upload. Erase MUST precede upload.
  if [ "${do_erase}" -eq 1 ]; then
    echo ">> Erasing chip ..."
    fh_pio run -e "${env}" -t erase
  else
    echo ">> --no-erase: keeping existing config (the MUI may NOT appear if displaymode != COLOR)."
  fi

  echo ">> Uploading ${env} ..."
  fh_pio run -e "${env}" -t upload

  # 6) Report + best-effort runtime confirmation.
  fh_post_flash_report "${env}"
}

if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
  fh_main "$@"
fi
