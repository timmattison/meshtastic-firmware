#!/usr/bin/env python3

"""Tests for bin/build_info.py (see GitHub issue #8).

These cover the pure, importable core that isolates the two volatile version
macros (APP_VERSION, BUILD_EPOCH) into a single generated translation unit so
they no longer land on the global src/ CCFLAGS and force a full recompile on
every commit / every day.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import build_info

# Sample inputs reused across the flag tests.
SAMPLE_VERSION_SHORT = "2.8.0"
SAMPLE_VERSION_LONG = "2.8.0.abc1234"
SAMPLE_ENV = "tbeam"
SAMPLE_REPO = "meshtastic/firmware"
SAMPLE_PREF_FLAG = "-DUSERPREFS_EXAMPLE=1"
SAMPLE_EPOCH_INT = 1753315200
SAMPLE_EPOCH_STR = "1753315200"


def test_global_flags_exclude_volatile_macros():
    """The global CCFLAGS must NOT carry -DAPP_VERSION= or -DBUILD_EPOCH=.

    Those two are the volatile values (git SHA every commit, epoch every day)
    that issue #8 moves into the generated TU. Using startswith("-DAPP_VERSION=")
    avoids matching the legitimate -DAPP_VERSION_SHORT= flag.
    """
    flags = build_info.assemble_global_flags(
        SAMPLE_VERSION_SHORT,
        SAMPLE_ENV,
        SAMPLE_REPO,
        pref_flags=(SAMPLE_PREF_FLAG,),
    )
    assert not any(
        f.startswith("-DAPP_VERSION=") for f in flags
    ), f"-DAPP_VERSION= must not be on the global flags: {flags}"
    assert not any(
        f.startswith("-DBUILD_EPOCH=") for f in flags
    ), f"-DBUILD_EPOCH= must not be on the global flags: {flags}"


def test_global_flags_keep_stable_macros_and_prefs():
    """The stable macros and every pref flag stay on the global CCFLAGS."""
    flags = build_info.assemble_global_flags(
        SAMPLE_VERSION_SHORT,
        SAMPLE_ENV,
        SAMPLE_REPO,
        pref_flags=(SAMPLE_PREF_FLAG,),
    )
    assert "-DAPP_VERSION_SHORT=" + SAMPLE_VERSION_SHORT in flags, flags
    assert "-DAPP_ENV=" + SAMPLE_ENV in flags, flags
    assert "-DAPP_REPO=" + SAMPLE_REPO in flags, flags
    assert SAMPLE_PREF_FLAG in flags, flags


def test_render_build_info_cpp_bakes_volatile_values():
    """The generated TU includes the header and bakes the volatile values."""
    cpp = build_info.render_build_info_cpp(SAMPLE_VERSION_LONG, SAMPLE_EPOCH_INT)
    assert '#include "build_info.h"' in cpp, cpp
    assert (
        'const char *const meshtastic_build_version = "2.8.0.abc1234";' in cpp
    ), cpp
    assert (
        'const char *const meshtastic_build_epoch_str = "1753315200";' in cpp
    ), cpp
    assert "const uint32_t meshtastic_build_epoch = 1753315200u;" in cpp, cpp


def test_render_build_info_cpp_epoch_int_and_str_are_identical():
    """build_epoch may arrive as an int or a numeric string; output must match."""
    from_int = build_info.render_build_info_cpp(SAMPLE_VERSION_LONG, SAMPLE_EPOCH_INT)
    from_str = build_info.render_build_info_cpp(SAMPLE_VERSION_LONG, SAMPLE_EPOCH_STR)
    assert from_int == from_str, (from_int, from_str)


def _read_source(name):
    with open(os.path.join(os.path.dirname(__file__), name)) as f:
        return f.read()


def test_platformio_custom_uses_assemble_global_flags_no_global_volatile():
    """Regression guard (issue #8): the build script must assemble its GLOBAL projenv
    flags via assemble_global_flags() and must never re-introduce the volatile
    -DAPP_VERSION= / -DBUILD_EPOCH= macros onto the global compile line. (The separate
    meshtastic-device-ui lib injection uses a ("APP_VERSION", ...) tuple, not the
    -DAPP_VERSION= string, so it is intentionally not matched here.)"""
    src = _read_source("platformio-custom.py")
    assert "assemble_global_flags(" in src, "build script no longer calls assemble_global_flags()"
    assert "-DAPP_VERSION=" not in src, "global -DAPP_VERSION= injection present/re-introduced"
    assert "-DBUILD_EPOCH=" not in src, "global -DBUILD_EPOCH= injection present/re-introduced"


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(1 if failed else 0)
