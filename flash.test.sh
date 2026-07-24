#!/usr/bin/env bash
#
# Behavioral + unit tests for flash.sh.
#
# flash.sh's job is to make a Meshtastic MUI (color-UI) install come up
# correctly on the FIRST try. Two properties matter, and both are tested
# here without any real PlatformIO install or hardware:
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
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}" # pin cwd so pio.sh's git rev-parse resolves to this worktree
flash_sh="${here}/flash.sh"

fail=0
note_fail() {
  echo "FAIL: $*" >&2
  fail=1
}

# --- 1. Orchestration: erase-before-upload, via a recording `pio` stub ----
# Parallel-safe: a unique stub dir + record file per run (mktemp), so two
# concurrent copies of this test never clobber each other's `pio` or log.
stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/flash-sh-test.XXXXXXXX")"
export FLASH_TEST_REC="${stub_dir}/calls.log"
: >"${FLASH_TEST_REC}"
trap 'rm -rf "${stub_dir}"' EXIT

cat >"${stub_dir}/pio" <<'STUB'
#!/usr/bin/env bash
echo "ARGS=$*" >>"${FLASH_TEST_REC}"
STUB
chmod +x "${stub_dir}/pio"

# A non-tft env skips the binary guard, so this run exercises pure
# orchestration. -y skips the destructive-erase confirmation prompt.
PATH="${stub_dir}:${PATH}" "${flash_sh}" -e heltec-v3 -y >/dev/null 2>&1 || true

erase_line="$(grep -nF 'ARGS=run -e heltec-v3 -t erase' "${FLASH_TEST_REC}" | head -1 | cut -d: -f1 || true)"
upload_line="$(grep -nF 'ARGS=run -e heltec-v3 -t upload' "${FLASH_TEST_REC}" | head -1 | cut -d: -f1 || true)"

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
  { printf 'tftSetup\n'; seq 1 200000; } | fh_symbols_have_tftsetup
); then
  note_fail "fh_symbols_have_tftsetup must not SIGPIPE its producer under pipefail"
fi

if [ "${fail}" -eq 0 ]; then
  echo "PASS: flash.sh erases before upload and enforces the HAS_TFT guard"
fi
exit "${fail}"
