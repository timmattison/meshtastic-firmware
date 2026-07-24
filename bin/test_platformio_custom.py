#!/usr/bin/env python3

"""Tests for the build tooling in bin/ (see GitHub issue #8).

Most of these cover bin/build_info.py: the pure, importable core that isolates
the two volatile version macros (APP_VERSION, BUILD_EPOCH) into a single
generated translation unit so they no longer land on the global src/ CCFLAGS
and force a full recompile on every commit / every day.

The rest cover bin/run-build-tests.sh, the runner that makes this file (and
every other build-tooling test) actually reachable from CI.
"""

import os
import shutil
import subprocess
import sys
import tempfile

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

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD_TEST_RUNNER = os.path.join(REPO_ROOT, "bin", "run-build-tests.sh")
MAIN_MATRIX_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "main_matrix.yml")
# The build-tooling guard tests that must be discovered. Both were orphaned -
# runnable only by hand - until bin/run-build-tests.sh started auto-discovering
# them, which is exactly the failure mode the runner exists to prevent.
KNOWN_BUILD_TESTS = ("bin/test_platformio_custom.py", "pio.test.sh")
SUBPROCESS_TIMEOUT_SECONDS = 300


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


def _require_runner():
    """Fail with a message about the missing wiring, not an OSError from exec()."""
    assert os.path.isfile(BUILD_TEST_RUNNER), (
        f"{BUILD_TEST_RUNNER} does not exist: the build-tooling tests "
        "(bin/test_*.py, *.test.sh) have no runner, so nothing - CI included - "
        "can invoke them as a set"
    )
    return BUILD_TEST_RUNNER


def _run(argv, cwd=REPO_ROOT):
    return subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def _scratch_runner_root():
    """Copy the runner into a throwaway repo root so its discovery can be driven.

    The runner resolves its root from its own location, so a copy at
    <tmp>/bin/run-build-tests.sh discovers only the tests we plant in <tmp>.
    mkdtemp() gives a unique dir per run, so concurrent copies of this test never
    share a scratch tree. Returns the tmp root; the caller removes it.
    """
    root = tempfile.mkdtemp(prefix=f"build-tests-{os.getpid()}-")
    os.makedirs(os.path.join(root, "bin"))
    shutil.copy2(BUILD_TEST_RUNNER, os.path.join(root, "bin", "run-build-tests.sh"))
    return root


def test_build_test_runner_exists_and_is_executable():
    """A repo-level runner for the build-tooling tests must exist and be runnable."""
    runner = _require_runner()
    assert os.access(runner, os.X_OK), f"{runner} is not executable"


def test_build_test_runner_discovers_the_known_build_tests():
    """Discovery - not a hardcoded list - must pick up both existing guard tests."""
    runner = _require_runner()
    result = _run([runner, "--list"])
    assert result.returncode == 0, f"--list failed ({result.returncode}):\n{result.stdout}"
    listed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for known in KNOWN_BUILD_TESTS:
        assert known in listed, f"{known} was not discovered by the runner: {listed}"


def test_build_test_runner_fails_when_a_discovered_test_fails():
    """Mutation check: a failing discovered test must turn the runner's verdict red."""
    _require_runner()
    root = _scratch_runner_root()
    try:
        failing = os.path.join(root, "bin", "test_planted_failure.py")
        with open(failing, "w") as f:
            f.write("import sys\n\nsys.exit(1)\n")
        result = _run([os.path.join(root, "bin", "run-build-tests.sh")], cwd=root)
        assert result.returncode != 0, f"runner passed despite a failing test:\n{result.stdout}"
        assert "RESULT: RED" in result.stdout, result.stdout
        assert "test_planted_failure.py" in result.stdout, result.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_test_runner_fails_when_it_discovers_nothing():
    """A runner that finds no tests must fail loudly, never report a silent green."""
    _require_runner()
    root = _scratch_runner_root()
    try:
        result = _run([os.path.join(root, "bin", "run-build-tests.sh")], cwd=root)
        assert result.returncode != 0, f"runner passed having found no tests:\n{result.stdout}"
        assert "RESULT: RED" in result.stdout, result.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_matrix_workflow_runs_the_build_test_runner():
    """CI wiring (issue #8): the pre-merge gate must invoke the build-test runner.

    main_matrix.yml is the workflow that runs on push, pull_request and
    merge_group. If it never calls the runner, every build-tooling guard test is
    decorative - it can only fail on a developer's machine, never in CI.
    """
    with open(MAIN_MATRIX_WORKFLOW) as f:
        workflow = f.read()
    assert "bin/run-build-tests.sh" in workflow, (
        f"{MAIN_MATRIX_WORKFLOW} never invokes bin/run-build-tests.sh: the "
        "build-tooling guard tests cannot fail CI"
    )


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
