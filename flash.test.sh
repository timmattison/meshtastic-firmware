#!/usr/bin/env bash
#
# Behavioral + unit tests for flash.sh.
#
# flash.sh's job is to make a Meshtastic MUI (color-UI) install come up
# correctly on the FIRST try. Three properties matter, and all are tested
# here without any real PlatformIO install, meshtastic CLI, or hardware:
#
#   1. Orchestration: it must ERASE the chip before it uploads. A fresh
#      config is what makes a HAS_TFT build auto-select displaymode=COLOR
#      on first boot; an app-only upload leaves a stale/poisoned displaymode
#      behind and you boot into the classic UI. We stub `pio` on PATH and
#      record every invocation, then assert the erase call precedes the
#      upload call -- observing the real mechanism, not a print statement.
#
#   2. The enforced HAS_TFT guard: `fh_is_mui_env` must recognise *-tft
#      envs, and `fh_symbols_have_tftsetup` must ACCEPT an ELF symbol dump
#      containing tftSetup and REJECT one that does not (the mutation case
#      -- proof the guard can actually fail, not just pass).
#
#   3. --region: the erase wipes the LoRa region, which leaves the radio
#      unable to transmit. --region sets it back after the upload. A bad
#      region name or a missing meshtastic CLI must abort BEFORE the
#      destructive erase, never after -- discovering a typo only once the
#      device has been wiped is the failure mode worth designing out.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}" # pin cwd so pio.sh's git rev-parse resolves to this worktree
flash_sh="${here}/flash.sh"

fail=0
note_fail() {
  echo "FAIL: $*" >&2
  fail=1
}

# Parallel-safe: a unique stub dir + record file per run (mktemp), so two
# concurrent copies of this test never clobber each other's stubs or logs.
stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/flash-sh-test.XXXXXXXX")"
trap 'rm -rf "${stub_dir}"' EXIT

cat >"${stub_dir}/pio" <<'STUB'
#!/usr/bin/env bash
echo "ARGS=$*" >>"${FLASH_TEST_REC}"
STUB
chmod +x "${stub_dir}/pio"

# Stand-in for the meshtastic CLI. Records what it was asked to do, and
# answers --get so flash.sh can read back what it set.
cat >"${stub_dir}/meshtastic-stub" <<'STUB'
#!/usr/bin/env bash
echo "MT=$*" >>"${FLASH_TEST_REC}"
case "$*" in
  *"--get lora.region"*) echo "lora.region: US" ;;
  *"--get display.displaymode"*) echo "display.displaymode: COLOR" ;;
esac
STUB
chmod +x "${stub_dir}/meshtastic-stub"

# run_flash <recfile> <flash.sh args...> -- run flash.sh against the stubs.
# Returns flash.sh's exit status; the recfile captures the call sequence.
run_flash() {
  local rec="$1"
  shift
  : >"${rec}"
  FLASH_TEST_REC="${rec}" \
    FH_REGION_RETRIES=1 FH_REGION_DELAY=0 \
    PATH="${stub_dir}:${PATH}" \
    "${flash_sh}" "$@" >/dev/null 2>&1
}

# line_of <recfile> <fixed-string> -- 1-based line number of first match, or "".
line_of() {
  grep -nF "$2" "$1" 2>/dev/null | head -1 | cut -d: -f1 || true
}

# --- 1. Orchestration: erase-before-upload -------------------------------
# A non-tft env skips the binary guard, so this run exercises pure
# orchestration. -y skips the destructive-erase confirmation prompt.
rec1="${stub_dir}/calls1.log"
run_flash "${rec1}" -e heltec-v3 -y || true

erase_line="$(line_of "${rec1}" 'ARGS=run -e heltec-v3 -t erase')"
upload_line="$(line_of "${rec1}" 'ARGS=run -e heltec-v3 -t upload')"

[ -n "${erase_line}" ] || note_fail "flash.sh did not erase the chip (no 'run -e heltec-v3 -t erase' call)"
[ -n "${upload_line}" ] || note_fail "flash.sh did not upload (no 'run -e heltec-v3 -t upload' call)"
if [ -n "${erase_line}" ] && [ -n "${upload_line}" ] && [ "${erase_line}" -ge "${upload_line}" ]; then
  note_fail "flash.sh uploaded before erasing (erase@${erase_line} not before upload@${upload_line})"
fi

# --- 2. Enforced HAS_TFT guard (pure functions) --------------------------
# shellcheck source=/dev/null
source "${flash_sh}"

fh_is_mui_env "t-deck-tft" || note_fail "fh_is_mui_env should treat 't-deck-tft' as an MUI env"
if fh_is_mui_env "heltec-v3"; then note_fail "fh_is_mui_env should NOT treat 'heltec-v3' as an MUI env"; fi

printf '420c9138 T _Z8tftSetupv\n' | fh_symbols_have_tftsetup \
  || note_fail "fh_symbols_have_tftsetup should ACCEPT a dump containing tftSetup"
# Mutation: a dump WITHOUT tftSetup must be rejected, or the guard is useless.
if printf '00000000 T _Z4mainv\n' | fh_symbols_have_tftsetup; then
  note_fail "fh_symbols_have_tftsetup should REJECT a dump lacking tftSetup (guard never fails)"
fi

# Regression: fh_main runs the guard under `set -o pipefail` as `nm <elf> |
# fh_symbols_have_tftsetup`. If the matcher exits early (grep -q) it SIGPIPEs
# nm, and pipefail then reports the whole pipeline as FAILED even though the
# symbol matched -- which would make the guard reject a valid MUI binary. A
# large producer whose match comes first must still succeed under pipefail.
if ! (
  set -o pipefail
  {
    printf 'tftSetup\n'
    seq 1 200000
  } | fh_symbols_have_tftsetup
); then
  note_fail "fh_symbols_have_tftsetup must not SIGPIPE its producer under pipefail"
fi

# --- 3. --region ---------------------------------------------------------
# Region names are derived from the protobuf header, so the accepted set
# cannot drift as new regions are added upstream.
fh_is_valid_region "US" || note_fail "fh_is_valid_region should accept 'US'"
fh_is_valid_region "EU_868" || note_fail "fh_is_valid_region should accept 'EU_868'"
# Mutation: a bogus name must be rejected, or validation is decorative.
if fh_is_valid_region "NOT_A_REGION"; then
  note_fail "fh_is_valid_region should REJECT 'NOT_A_REGION' (validation never fails)"
fi
# UNSET is the "no region" value -- accepting it would defeat the flag.
if fh_is_valid_region "UNSET"; then
  note_fail "fh_is_valid_region should REJECT 'UNSET'"
fi

# --region must set the region, and only AFTER the upload has landed.
rec3="${stub_dir}/calls3.log"
FH_MESHTASTIC_BIN="${stub_dir}/meshtastic-stub" run_flash "${rec3}" -e heltec-v3 -y --region US || true

up3="$(line_of "${rec3}" 'ARGS=run -e heltec-v3 -t upload')"
set3="$(line_of "${rec3}" 'MT=--set lora.region US')"
[ -n "${set3}" ] || note_fail "--region US did not set the region (no 'meshtastic --set lora.region US' call)"
if [ -n "${up3}" ] && [ -n "${set3}" ] && [ "${set3}" -le "${up3}" ]; then
  note_fail "--region set the region before the upload (set@${set3} not after upload@${up3})"
fi

# A bogus region must abort BEFORE the erase -- never wipe a device and only
# then discover the region name was a typo.
rec4="${stub_dir}/calls4.log"
if FH_MESHTASTIC_BIN="${stub_dir}/meshtastic-stub" run_flash "${rec4}" -e heltec-v3 -y --region BOGUS_REGION; then
  note_fail "--region BOGUS_REGION should fail, but flash.sh exited 0"
fi
if [ -n "$(line_of "${rec4}" 'ARGS=run -e heltec-v3 -t erase')" ]; then
  note_fail "--region BOGUS_REGION erased the chip before validating the region"
fi

# A missing meshtastic CLI must fail loudly and BEFORE the erase, rather than
# silently skipping the region step and leaving a wiped, non-transmitting radio.
rec5="${stub_dir}/calls5.log"
if FH_MESHTASTIC_BIN="${stub_dir}/definitely-not-installed" run_flash "${rec5}" -e heltec-v3 -y --region US; then
  note_fail "--region with no meshtastic CLI should fail, but flash.sh exited 0"
fi
if [ -n "$(line_of "${rec5}" 'ARGS=run -e heltec-v3 -t erase')" ]; then
  note_fail "--region with no meshtastic CLI erased the chip before checking for the CLI"
fi

# Without --region, nothing should touch the meshtastic CLI.
if [ -n "$(line_of "${rec1}" 'MT=--set')" ]; then
  note_fail "flash.sh set a region even though --region was not passed"
fi

if [ "${fail}" -eq 0 ]; then
  echo "PASS: flash.sh erases before upload, enforces the HAS_TFT guard, and sets --region safely"
fi
exit "${fail}"
