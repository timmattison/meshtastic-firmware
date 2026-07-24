#!/usr/bin/env bash
#
# Mutation test for the compile-time gesture-conflict guard in
# src/modules/VoiceMemoModule.h.
#
# The guard fails the build with a #error whenever both Voice Memo and Games are
# compiled into one image, because both bind INPUT_BROKER_SELECT_LONG and the
# InputBroker observer chain short-circuits on the first consumer that returns
# non-zero (Observer.h) -- so one would silently shadow the other's long-press.
#
# We compile tiny translation units against the REAL header. configuration.h
# needs Arduino.h and won't preprocess standalone on macOS, so we shadow it with
# a minimal stub placed earlier on the include path (-I "$stub_dir" before
# -I src). The stub still defines BASEUI_HAS_GAMES (0/1) exactly like the real
# one, which is all the guard reads. We assert on OUR compiler-agnostic #error
# text, never on any compiler-specific diagnostic.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}" # pin cwd so -I src resolves against this worktree

cxx="${CXX:-c++}"
err_substr="Voice Memo and Games both bind"

# Parallel-safe scratch: mktemp -d gives a unique dir per run, so two copies of
# this test running concurrently never share the stub or TU paths.
stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/vm-guard-test.XXXXXXXX")"
trap 'rm -rf "${stub_dir}"' EXIT

# Minimal configuration.h shadow: the guard only reads BASEUI_HAS_GAMES, and the
# real header defines it (0/1) via the same #ifndef idiom.
cat >"${stub_dir}/configuration.h" <<'STUB'
#pragma once
#ifndef BASEUI_HAS_GAMES
#define BASEUI_HAS_GAMES 0
#endif
STUB

# Compile one TU (passed on stdin) with the stub shadowing src/configuration.h.
# Captures combined stdout+stderr; returns the compiler's exit status.
compile_tu() {
  "${cxx}" -std=c++17 -fsyntax-only -x c++ -I "${stub_dir}" -I src - 2>&1
}

fail=0

# --- Check 1: positive -- both modules present MUST trip the guard. ----------
out1="$(printf '%s\n' \
  '#define MESHTASTIC_HAS_VOICEMEMO 1' \
  '#define BASEUI_HAS_GAMES 1' \
  '#include "modules/VoiceMemoModule.h"' | compile_tu && echo __RC0__ || true)"
if grep -qF '__RC0__' <<<"${out1}"; then
  echo "FAIL: guard did NOT fail the build when both modules are compiled in" >&2
  fail=1
elif ! grep -qF "${err_substr}" <<<"${out1}"; then
  echo "FAIL: build failed but without our #error text (expected: '${err_substr}')" >&2
  echo "      got: ${out1}" >&2
  fail=1
fi

# --- Check 2: control gated on games -- Voice Memo alone MUST NOT trip it. ----
# (Compilation still fails on missing Arduino includes; we assert only that OUR
#  #error is ABSENT. Catches a mutation that drops the BASEUI_HAS_GAMES term.)
out2="$(printf '%s\n' \
  '#define MESHTASTIC_HAS_VOICEMEMO 1' \
  '#include "modules/VoiceMemoModule.h"' | compile_tu || true)"
if grep -qF "${err_substr}" <<<"${out2}"; then
  echo "FAIL: guard fired for Voice Memo alone (games not enabled)" >&2
  echo "      got: ${out2}" >&2
  fail=1
fi

# --- Check 3: control with neither macro -- header MUST compile cleanly. ------
# (The #ifdef MESHTASTIC_HAS_VOICEMEMO body -- and its heavy includes -- is
#  skipped, so this is a clean exit 0 with no #error.)
if out3="$(printf '%s\n' \
  '#include "modules/VoiceMemoModule.h"' | compile_tu)"; then
  if grep -qF "${err_substr}" <<<"${out3}"; then
    echo "FAIL: guard fired when neither macro is set" >&2
    echo "      got: ${out3}" >&2
    fail=1
  fi
else
  echo "FAIL: header did not compile cleanly with neither macro set" >&2
  echo "      got: ${out3}" >&2
  fail=1
fi

if [[ "${fail}" -eq 0 ]]; then
  echo "PASS: gesture-conflict guard fires iff both Voice Memo and Games are compiled in"
fi
exit "${fail}"
