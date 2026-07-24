#!/usr/bin/env bash
# Run every build-tooling test in this repo and emit a single, unambiguous verdict.
#
# Why this exists: bin/run-tests.sh runs the *firmware* tests - it enumerates the
# test/test_* suite directories and hands them to PlatformIO. Tests that cover the build
# tooling itself (the scripts under bin/, the pio.sh wrapper) live outside that tree, so
# nothing ran them. bin/test_build_tooling.py, bin/test_size_scripts.py and
# pio.test.sh were all orphaned that way: green forever, because they only ran when
# somebody remembered to type their name. A regression guard nobody invokes is
# decorative - the guard against re-introducing the volatile -DAPP_VERSION= /
# -DBUILD_EPOCH= macros (issue #8) could not have failed CI.
#
# So this runner DISCOVERS its tests instead of listing them. Drop a new file matching
# one of the patterns below and it is enrolled automatically - there is no registry to
# forget to update, which is the whole point:
#
#   bin/test_*.py    ->  python3   (Python test modules; each exits non-zero on failure)
#   *.test.sh        ->  bash      (shell test scripts at the repo root)
#   bin/*.test.sh    ->  bash      (shell test scripts alongside the build scripts)
#
# Discovering zero tests is itself a failure. A runner that reports green because it
# found nothing is exactly the bug this script exists to prevent, one level up.
#
# Every test is run even after one fails, so a single run reports all the breakage.
# Output is captured and echoed (indented) only for tests that fail, keeping a green run
# to one line per test; --verbose echoes it for passing tests too.
#
# Usage:
#   ./bin/run-build-tests.sh            # run every discovered test, print the verdict
#   ./bin/run-build-tests.sh --list     # print the discovered tests, run nothing
#   ./bin/run-build-tests.sh --verbose  # also echo the output of passing tests
#
# Exit codes: 0 = GREEN, 1 = RED.
#
# The final line is machine-readable, e.g.:
#   RESULT: GREEN 3/3 build tests passed
#   RESULT: RED 1/3 build tests failed (pio.test.sh)
#   RESULT: RED no build tests discovered - discovery is broken

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR" || exit 1

LIST_ONLY=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
	case "$1" in
	--list)
		LIST_ONLY=true
		shift
		;;
	--verbose | -v)
		VERBOSE=true
		shift
		;;
	*)
		echo "usage: $0 [--list] [--verbose]" >&2
		exit 1
		;;
	esac
done

# --- Discovery ---------------------------------------------------------------
# Plain globs (not `find -printf`, which is GNU-only and breaks on macOS). nullglob makes
# a pattern that matches nothing expand to nothing instead of to itself; the shell sorts
# each expansion, so the run order is deterministic. The runner itself matches none of
# these patterns, so it cannot discover and re-invoke itself.
DISCOVERY_PATTERNS="bin/test_*.py, *.test.sh, bin/*.test.sh"
shopt -s nullglob
TESTS=(bin/test_*.py *.test.sh bin/*.test.sh)
shopt -u nullglob

if [[ ${#TESTS[@]} -eq 0 ]]; then
	echo "RESULT: RED no build tests discovered (looked for: ${DISCOVERY_PATTERNS}) - discovery is broken"
	exit 1
fi

if $LIST_ONLY; then
	printf '%s\n' "${TESTS[@]}"
	exit 0
fi

# --- Run ---------------------------------------------------------------------
# Interpreter by extension rather than by exec bit: a test that lost its +x (or arrived
# via an archive that dropped it) must still run, not silently vanish from the set.
run_one() {
	case "$1" in
	*.py) python3 "$1" ;;
	*) bash "$1" ;;
	esac
}

LOG="$(mktemp -t meshbuildtest.XXXXXX.log)"
trap 'rm -f "$LOG"' EXIT

FAILED=()
for t in "${TESTS[@]}"; do
	run_one "$t" >"$LOG" 2>&1
	rc=$?
	if [[ $rc -eq 0 ]]; then
		echo "PASS: $t"
		if $VERBOSE; then sed 's/^/    /' "$LOG"; fi
	else
		echo "FAIL: $t (exit $rc)"
		sed 's/^/    /' "$LOG"
		FAILED+=("$t")
	fi
done

TOTAL=${#TESTS[@]}
FAIL_COUNT=${#FAILED[@]}

echo ""
if [[ $FAIL_COUNT -gt 0 ]]; then
	echo "RESULT: RED ${FAIL_COUNT}/${TOTAL} build tests failed (${FAILED[*]})"
	exit 1
fi

echo "RESULT: GREEN ${TOTAL}/${TOTAL} build tests passed"
exit 0
