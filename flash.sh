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
#   ./flash.sh -e t-deck-tft --region US           # ...and restore the LoRa region
#   ./flash.sh -e t-deck-tft --port /dev/cu.usbmodemXXXX
#   ./flash.sh -e t-deck-tft -y                    # skip the erase confirmation
#   ./flash.sh -e t-deck --no-erase                # advanced: keep config (may NOT show MUI)
#
# WARNING: the erase wipes ALL device config -- LoRa region, WiFi creds,
# channels, node identity. That is intended for a fresh install / recovering a
# device.
#
# --region <CODE> restores the LoRa region once the upload lands, so the radio
# can transmit again (without it the device comes up with region UNSET and
# stays silent). It needs the `meshtastic` CLI, and both the region name and
# the CLI are checked BEFORE the erase, so a typo can never leave you with a
# wiped device. Region is locale-legal, not cosmetic, so there is deliberately
# no default -- name yours explicitly. WiFi credentials still need re-entering
# by hand.
# NB: no top-level `set` here on purpose -- this file is sourced by
# flash.test.sh, so it must not mutate the caller's shell options. Strict mode
# is enabled inside fh_main(), which is the only thing that runs on execution.

FLASH_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PIO_SH="${FLASH_SH_DIR}/pio.sh"

fh_usage() {
  sed -n '2,44p' "${BASH_SOURCE[0]:-$0}" | sed 's/^# \{0,1\}//'
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
#
# Deliberately NOT `grep -q`: the caller runs `nm <elf> | fh_symbols_...`
# under `set -o pipefail`, and -q would exit on first match, SIGPIPE nm, and
# make pipefail report the pipeline as failed. Plain grep drains all input, so
# nm exits cleanly and the pipeline status reflects only the match result.
fh_symbols_have_tftsetup() {
  grep 'tftSetup' >/dev/null 2>&1
}

# fh_valid_regions -> newline-separated LoRa region names, read from the
# generated protobuf header so the accepted set never drifts as new regions
# are added upstream. UNSET is excluded: it is the "no region" value, and
# setting it would leave the radio silent, which is what --region prevents.
# Returns non-zero if the header cannot be read.
fh_valid_regions() {
  local hdr="${FLASH_SH_DIR}/src/mesh/generated/meshtastic/config.pb.h"
  [ -r "${hdr}" ] || return 1
  grep -oE 'RegionCode_[A-Z0-9_]+ = [0-9]+' "${hdr}" |
    sed 's/RegionCode_//; s/ = [0-9]*//' |
    grep -vx 'UNSET' |
    sort -u
}

# fh_is_valid_region <name> -> 0 if <name> is a settable LoRa region.
# If the protobuf header is unavailable (e.g. flash.sh copied out of the
# repo) validation is skipped rather than blocking a legitimate region --
# the CLI itself will reject a genuinely bad name in that case.
fh_is_valid_region() {
  local want="${1:-}" regions
  [ -n "${want}" ] || return 1
  if [ "${want}" = "UNSET" ]; then
    return 1
  fi
  regions="$(fh_valid_regions 2>/dev/null || true)"
  [ -n "${regions}" ] || return 0
  # Drains its input on purpose (see fh_symbols_have_tftsetup).
  printf '%s\n' "${regions}" | grep -x -- "${want}" >/dev/null 2>&1
}

# fh_meshtastic_bin -> the meshtastic CLI to drive (overridable for tests).
fh_meshtastic_bin() {
  printf '%s\n' "${FH_MESHTASTIC_BIN:-meshtastic}"
}

# fh_have_meshtastic_cli -> 0 if the meshtastic CLI is runnable.
fh_have_meshtastic_cli() {
  command -v "$(fh_meshtastic_bin)" >/dev/null 2>&1
}

# fh_meshtastic <args...> -> run the CLI against the selected port.
fh_meshtastic() {
  local bin
  bin="$(fh_meshtastic_bin)"
  if [ -n "${FH_PORT}" ]; then
    "${bin}" --port "${FH_PORT}" "$@"
  else
    "${bin}" "$@"
  fi
}

# fh_set_region <region> -> set lora.region on the freshly flashed device.
# Retries: right after a flash the device is still booting and its USB serial
# will refuse or time out the first connection attempts, so a single try is
# not enough to be reliable. Returns non-zero if every attempt fails.
fh_set_region() {
  local region="$1" attempt=1 tries delay
  tries="${FH_REGION_RETRIES:-6}"
  delay="${FH_REGION_DELAY:-10}"
  while [ "${attempt}" -le "${tries}" ]; do
    if fh_meshtastic --set lora.region "${region}" >/dev/null 2>&1; then
      echo "  set lora.region = ${region}"
      return 0
    fi
    if [ "${attempt}" -lt "${tries}" ]; then
      echo "  device not ready yet (attempt ${attempt}/${tries}); retrying in ${delay}s..."
      if [ "${delay}" -gt 0 ]; then
        sleep "${delay}"
      fi
    fi
    attempt=$((attempt + 1))
  done
  return 1
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
  # `|| true` neutralises the SIGPIPE find gets from head under pipefail.
  elf="$(find ".pio/build/${env}" -maxdepth 1 -name '*.elf' 2>/dev/null | head -1 || true)"
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
  if [ -n "${FH_REGION}" ]; then
    local got
    got="$(fh_meshtastic --get lora.region 2>/dev/null | tr -d '[:space:]' || true)"
    case "${got}" in
      *"${FH_REGION}"*) echo "  ok: lora.region = ${FH_REGION} (radio can transmit)." ;;
      *) echo "  note: set lora.region=${FH_REGION}, but could not read it back to confirm." ;;
    esac
  else
    echo "  Config was erased -- set your region before the radio will transmit, e.g.:"
    echo "    meshtastic --set lora.region US"
    echo "    (or re-run flash.sh with --region US to have it done automatically)"
  fi
  echo "  WiFi credentials and channels were erased too; re-enter them if you used them."
  if fh_is_mui_env "${env}" && fh_have_meshtastic_cli; then
    local dm
    dm="$(fh_meshtastic --get display.displaymode 2>/dev/null | tr -d '[:space:]' || true)"
    case "${dm}" in
      *COLOR*) echo "  ok: display.displaymode = COLOR (MUI active)." ;;
      "") echo "  note: could not read displaymode (device still booting) -- the erase+guard still guarantee it." ;;
      *) echo "  warning: display.displaymode = ${dm} (expected COLOR). Set it with: meshtastic --set display.displaymode COLOR" ;;
    esac
  elif fh_is_mui_env "${env}"; then
    echo "  (Runtime displaymode check skipped: no 'meshtastic' CLI on PATH. Correctness is"
    echo "   guaranteed by the erase -> fresh config -> COLOR default, plus the HAS_TFT guard.)"
  fi
}

fh_main() {
  set -euo pipefail
  local env="" assume_yes=0 do_erase=1
  FH_PORT=""
  FH_REGION=""
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
      --region)
        FH_REGION="${2:-}"
        shift 2
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

  # Validate --region up front -- BEFORE the build and, crucially, before the
  # destructive erase. A typo or a missing CLI must never be discovered after
  # the device has already been wiped of its region.
  if [ -n "${FH_REGION}" ]; then
    if ! fh_is_valid_region "${FH_REGION}"; then
      echo "flash.sh: '${FH_REGION}' is not a valid LoRa region." >&2
      echo "          Valid regions:" >&2
      fh_valid_regions 2>/dev/null | paste -sd' ' - | fold -s -w 68 | sed 's/^/            /' >&2
      return 2
    fi
    if ! fh_have_meshtastic_cli; then
      echo "flash.sh: --region ${FH_REGION} needs the 'meshtastic' CLI, which was not found." >&2
      echo "          Install it (e.g. 'uv tool install meshtastic') or set FH_MESHTASTIC_BIN," >&2
      echo "          or drop --region and set the region yourself after flashing." >&2
      return 1
    fi
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

  # 6) Restore the region the erase wiped, so the radio can transmit again.
  if [ -n "${FH_REGION}" ]; then
    echo ">> Setting lora.region = ${FH_REGION} ..."
    if ! fh_set_region "${FH_REGION}"; then
      echo "flash.sh: could not set lora.region=${FH_REGION} -- the device stayed unreachable." >&2
      echo "          The firmware flashed fine; set it once the device settles:" >&2
      echo "            meshtastic --set lora.region ${FH_REGION}" >&2
      return 1
    fi
  fi

  # 7) Report + best-effort runtime confirmation.
  fh_post_flash_report "${env}"
}

if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
  fh_main "$@"
fi
