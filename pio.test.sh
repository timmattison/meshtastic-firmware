#!/usr/bin/env bash
#
# Behavioral tests for pio.sh.
#
# Verifies the wrapper (a) isolates PLATFORMIO_CORE_DIR to a per-worktree
# location and (b) forwards its arguments to pio unchanged. No real PlatformIO
# install is needed: we put a stub `pio` on PATH that simply reports the env
# var and args it was handed, so the test observes the actual mechanism (env
# exported to the child process) rather than a print statement.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}" # pin cwd so both git rev-parse calls resolve to this worktree
pio_sh="${here}/pio.sh"

# Parallel-safe scratch: mktemp -d gives a unique dir per run, so two copies of
# this test running concurrently never share a `pio` stub path.
stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/pio-sh-test.XXXXXXXX")"
trap 'rm -rf "${stub_dir}"' EXIT

cat >"${stub_dir}/pio" <<'STUB'
#!/usr/bin/env bash
echo "CORE=${PLATFORMIO_CORE_DIR:-<unset>}"
echo "ARGS=$*"
STUB
chmod +x "${stub_dir}/pio"

output="$(PATH="${stub_dir}:${PATH}" "${pio_sh}" run -e t-deck-tft -t upload 2>/dev/null)"

expected_core="$(git rev-parse --show-toplevel)/.platformio"

fail=0

if ! grep -qxF "CORE=${expected_core}" <<<"${output}"; then
  echo "FAIL: PLATFORMIO_CORE_DIR not isolated to this worktree" >&2
  echo "      expected: CORE=${expected_core}" >&2
  echo "      got:      $(grep '^CORE=' <<<"${output}" || echo '<no CORE line>')" >&2
  fail=1
fi

if ! grep -qxF "ARGS=run -e t-deck-tft -t upload" <<<"${output}"; then
  echo "FAIL: arguments not forwarded to pio verbatim" >&2
  echo "      got: $(grep '^ARGS=' <<<"${output}" || echo '<no ARGS line>')" >&2
  fail=1
fi

if [[ "${fail}" -eq 0 ]]; then
  echo "PASS: pio.sh isolates the core dir per worktree and forwards args"
fi
exit "${fail}"
