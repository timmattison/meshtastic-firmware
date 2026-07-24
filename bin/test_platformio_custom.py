#!/usr/bin/env python3

"""Tests for the build tooling in bin/ (see GitHub issue #8).

Most of these cover bin/build_info.py: the pure, importable core that isolates
the two volatile version macros (APP_VERSION, BUILD_EPOCH) into a single
generated translation unit so they no longer land on the global src/ CCFLAGS
and force a full recompile on every commit / every day.

The rest cover bin/run-build-tests.sh, the runner that makes this file (and
every other build-tooling test) actually reachable from CI.
"""

import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

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
PLATFORMIO_INI_NAME = "platformio.ini"
BUILD_TEST_RUNNER = os.path.join(REPO_ROOT, "bin", "run-build-tests.sh")
MAIN_MATRIX_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "main_matrix.yml")
# The build-tooling guard tests that must be discovered. Both were orphaned -
# runnable only by hand - until bin/run-build-tests.sh started auto-discovering
# them, which is exactly the failure mode the runner exists to prevent.
KNOWN_BUILD_TESTS = ("bin/test_platformio_custom.py", "pio.test.sh")
SUBPROCESS_TIMEOUT_SECONDS = 300
CHECK_ALL_SCRIPT = os.path.join(REPO_ROOT, "bin", "check-all.sh")

# --- Repo-wide volatile-macro scan -------------------------------------------
# Every place a -D compile flag can reach a build. Globs are relative to the scanned
# root and are matched with pathlib.Path.glob, so "**" recurses. They are enumerated
# explicitly rather than by walking the tree: a bare walk would descend into the
# worktree-local .platformio/ toolchain install (thousands of vendored .ini/.py files)
# and report third-party noise the repo does not control.
BUILD_SURFACE_GLOBS = (
    "platformio.ini",
    "variants/**/*.ini",
    "bin/*.sh",
    "bin/*.py",
    "*.sh",
    "extra_scripts/*.py",
    ".github/workflows/*.yml",
    ".github/actions/**/*.yml",
)

# Files that contain the forbidden literals while *documenting* the ban rather than
# performing it. Kept as an explicit, exhaustive path list - never a wildcard or a
# directory - so a genuine re-introduction cannot hide behind a filename.
VOLATILE_SCAN_EXEMPT_PATHS = (
    # Defines VOLATILE_MACROS; its module and function docstrings quote the banned
    # flags to explain why they were removed from the global CCFLAGS.
    "bin/build_info.py",
    # This guard itself: its assertions and fixtures quote the banned flags.
    "bin/test_platformio_custom.py",
)

# Comment leaders per file type. Everything from the first leader on a line is dropped
# before matching (see test_volatile_macro_scan_ignores_disabled_and_lookalike_lines):
# an already-commented-out flag is disabled, so flagging it would be a false positive
# and would pressure someone into deleting a deliberately-preserved historical note.
# The trade-off is a possible false negative when a banned flag appears *after* a "#"
# inside a string literal; that is accepted because a real injection has to reach a
# compiler, and the disabling "#" always precedes the flag it disables.
COMMENT_LEADERS_BY_SUFFIX = {
    ".ini": ("#", ";"),
    ".sh": ("#",),
    ".py": ("#",),
    ".yml": ("#",),
}
DEFAULT_COMMENT_LEADERS = ("#",)


def volatile_macro_flag_patterns():
    """Compile the forbidden ``-D<macro>=`` patterns from build_info.VOLATILE_MACROS.

    Derived, never hardcoded: :data:`build_info.VOLATILE_MACROS` is the single
    definition of which macros are banned from any compile line (issue #8).

    ``-D\\s*`` also catches the spaced ``-D APP_VERSION=`` form that platformio.ini
    uses elsewhere. Requiring ``=`` immediately after the macro name is what keeps the
    legitimate, stable ``-DAPP_VERSION_SHORT=`` replacement from matching.

    Returns:
        A ``list[re.Pattern]``, one per entry of ``build_info.VOLATILE_MACROS``.
    """
    return [
        re.compile(r"-D\s*" + re.escape(macro) + "=") for macro in build_info.VOLATILE_MACROS
    ]


def _strip_comment(line, leaders):
    """Return ``line`` truncated at the earliest comment leader it contains."""
    cut = len(line)
    for leader in leaders:
        found = line.find(leader)
        if found != -1 and found < cut:
            cut = found
    return line[:cut]


def scan_build_surfaces_for_volatile_macros(root=REPO_ROOT):
    """Find every live ``-DAPP_VERSION=`` / ``-DBUILD_EPOCH=`` injection under ``root``.

    ``root`` is injectable so the scan can be pointed at a throwaway tree and proven
    capable of going red (a guard that cannot fail is worthless).

    Args:
        root: Directory to treat as the repository root. Defaults to this checkout.

    Returns:
        A sorted ``list[(relpath, lineno, matched_text, stripped_line)]`` of hits;
        empty when no build surface injects a volatile macro.
    """
    patterns = volatile_macro_flag_patterns()
    root_path = pathlib.Path(root)
    hits = []
    seen = set()
    for glob in BUILD_SURFACE_GLOBS:
        for path in sorted(root_path.glob(glob)):
            if not path.is_file():
                continue
            rel = path.relative_to(root_path).as_posix()
            if rel in seen or rel in VOLATILE_SCAN_EXEMPT_PATHS:
                continue
            seen.add(rel)
            leaders = COMMENT_LEADERS_BY_SUFFIX.get(path.suffix, DEFAULT_COMMENT_LEADERS)
            with open(path, errors="replace") as f:
                for lineno, raw in enumerate(f, 1):
                    code = _strip_comment(raw, leaders)
                    for pattern in patterns:
                        match = pattern.search(code)
                        if match:
                            hits.append((rel, lineno, match.group(0), raw.strip()))
    return sorted(hits)


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


def test_no_build_surface_injects_a_volatile_macro():
    """Repo-wide guard (issue #8): NO build surface may inject a volatile macro.

    The predecessor of this test read exactly one file, bin/platformio-custom.py.
    The invariant is repo-wide, so a one-file guard is the bug: the same forbidden
    -DAPP_VERSION= injection survived in bin/check-all.sh and the guard never saw it.
    This scans every surface a -D flag can reach a compiler from (see
    BUILD_SURFACE_GLOBS) and derives the forbidden list from
    build_info.VOLATILE_MACROS, so there is exactly one definition of what is banned.
    """
    hits = scan_build_surfaces_for_volatile_macros()
    detail = "\n".join(f"  {rel}:{line}: {text!r} in {src!r}" for rel, line, text, src in hits)
    assert not hits, (
        "volatile version macros are injected on a build compile line; they belong in "
        f"the generated src/build_info.cpp TU, not on any -D flag:\n{detail}"
    )


def test_check_all_passes_the_stable_version_macro_to_pio_check():
    """bin/check-all.sh must define APP_VERSION_SHORT, the macro configuration.h needs.

    `pio check --flags` REPLACES platformio.ini's check_flags rather than appending to
    them (platformio/check/cli.py: `flags=flags or env_options.get("check_flags")`), so
    the -DAPP_VERSION_SHORT=1.0.0 in check_flags does not reach cppcheck from here.
    Without it, src/configuration.h's `#error APP_VERSION_SHORT must be set by the build
    environment` fires; cppcheck reports that as a high-severity
    preprocessorErrorDirective and --fail-on-defect=high fails the run on every board.
    """
    with open(CHECK_ALL_SCRIPT) as f:
        script = f.read()
    assert "-DAPP_VERSION_SHORT=" in script, (
        f"{CHECK_ALL_SCRIPT} never defines APP_VERSION_SHORT for cppcheck, so "
        "src/configuration.h's #error guard fires for every board it checks"
    )


def _scratch_surface_root():
    """A throwaway repo root for driving scan_build_surfaces_for_volatile_macros().

    mkdtemp() yields a unique directory per call, so concurrent copies of these tests
    never share a scratch tree (same parallel-safety reasoning as _scratch_runner_root).
    Returns the tmp root; the caller removes it.
    """
    return tempfile.mkdtemp(prefix=f"surface-scan-{os.getpid()}-")


def _plant(root, relpath, text):
    """Write ``text`` to ``root/relpath``, creating parent directories as needed."""
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# One planted violation per surface kind, to prove the scan reaches all of them.
PLANTED_VIOLATIONS = {
    "platformio.ini": "build_flags =\n\t-DAPP_VERSION=2.8.0.abc1234\n",
    "variants/esp32/planted/platformio.ini": "build_flags = -D BUILD_EPOCH=$UNIX_TIME\n",
    "bin/planted.sh": 'pio check --flags "-DAPP_VERSION=${APP_VERSION}"\n',
    "bin/planted.py": 'env.Append(CCFLAGS=["-DAPP_VERSION=" + ver])\n',
    "planted.sh": 'CCFLAGS="-DBUILD_EPOCH=$(date +%s)"\n',
    "extra_scripts/planted.py": 'env.Append(CCFLAGS=["-DBUILD_EPOCH=" + epoch])\n',
    ".github/workflows/planted.yml": "      - run: pio run --project-option=build_flags=-DAPP_VERSION=x\n",
    ".github/actions/planted/action.yml": "runs:\n  steps:\n    - run: echo -DBUILD_EPOCH=1\n",
}


def test_volatile_macro_scan_flags_a_planted_violation_on_every_surface():
    """Mutation check: the guard must actually go red when a violation exists.

    A guard that can never fail is worthless, so plant one violation per surface kind
    in a throwaway root and require every one of them to be reported.
    """
    root = _scratch_surface_root()
    try:
        for relpath, text in PLANTED_VIOLATIONS.items():
            _plant(root, relpath, text)
        flagged = {rel for rel, _, _, _ in scan_build_surfaces_for_volatile_macros(root)}
        missed = sorted(set(PLANTED_VIOLATIONS) - flagged)
        assert not missed, f"the scan did not reach these planted violations: {missed}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# Content that legitimately contains the macro names and must NEVER be flagged.
LEGITIMATE_LOOKALIKES = {
    # Already commented out: the flag is disabled, so there is nothing to fix.
    "platformio.ini": (
        "build_flags =\n"
        "\t-DAPP_VERSION_SHORT=1.0.0\n"
        "\t#-DBUILD_EPOCH=$UNIX_TIME ; set in platformio-custom.py now\n"
        "\t-DMAX_THREADS=40 ; -DAPP_VERSION= used to live here\n"
    ),
    # A shell variable is not a compile flag, and prose in a comment is not an injection.
    "bin/planted-legit.sh": (
        "export APP_VERSION=$VERSION\n"
        "# never re-introduce -DAPP_VERSION= or -DBUILD_EPOCH= on the global flags\n"
        'pio check --flags "-DAPP_VERSION_SHORT=${APP_VERSION}"\n'
    ),
    # Exempt paths document the ban and must stay readable.
    "bin/build_info.py": 'VOLATILE_MACROS = ("APP_VERSION", "BUILD_EPOCH")  # bans -DAPP_VERSION=\n',
    "bin/test_platformio_custom.py": 'assert "-DBUILD_EPOCH=" not in flags\n',
}


def test_volatile_macro_scan_ignores_disabled_and_lookalike_lines():
    """The guard must not flag disabled lines, shell vars, prose, or the SHORT macro.

    Four distinct false positives, all deliberately excluded:
      * an already-commented-out flag (comments are stripped before matching),
      * `export APP_VERSION=...`, a shell variable with no -D prefix,
      * prose in a comment that names the banned flags to document them,
      * -DAPP_VERSION_SHORT=, the correct stable replacement (the required "="
        immediately after the macro name is what distinguishes it).
    """
    root = _scratch_surface_root()
    try:
        for relpath, text in LEGITIMATE_LOOKALIKES.items():
            _plant(root, relpath, text)
        hits = scan_build_surfaces_for_volatile_macros(root)
        assert not hits, f"the scan flagged legitimate content: {hits}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_volatile_macro_scan_is_driven_by_build_info_volatile_macros():
    """The banned list must come from build_info.VOLATILE_MACROS, not a second copy.

    Proves the constant is load-bearing: a macro the scan ignores today starts being
    flagged the moment it is added to VOLATILE_MACROS, and nothing else needs editing.
    """
    root = _scratch_surface_root()
    original = build_info.VOLATILE_MACROS
    try:
        _plant(root, "bin/planted.py", '["-DAPP_VERSION_SHORT=1.0", "-DPLANTED_MACRO=1"]\n')
        assert not scan_build_surfaces_for_volatile_macros(
            root
        ), "-DPLANTED_MACRO= was flagged before it was declared volatile"
        build_info.VOLATILE_MACROS = original + ("PLANTED_MACRO",)
        hits = scan_build_surfaces_for_volatile_macros(root)
        assert [(rel, text) for rel, _, text, _ in hits] == [
            ("bin/planted.py", "-DPLANTED_MACRO=")
        ], f"adding a macro to VOLATILE_MACROS did not drive the scan: {hits}"
    finally:
        build_info.VOLATILE_MACROS = original
        shutil.rmtree(root, ignore_errors=True)


# --- extra_scripts ordering: the generated TU must exist before src/ is globbed ---
# PlatformIO's builder/main.py runs, in order:
#   env.SConscript(env.GetExtraScripts("pre"))   <- "pre:" entries
#   env.SConscript("$BUILD_SCRIPT")              <- BuildSources() globs $PROJECT_SRC_DIR
#   env.SConscript(env.GetExtraScripts("post"))  <- "post:" AND unprefixed entries
# The glob is eager (piobuild.py CollectBuildFiles -> fs.match_src_files returns a
# concrete list), and src/build_info.cpp is gitignored precisely because its content
# changes every commit. So a generator that runs anywhere but "pre:" writes the file
# after the file list is already fixed: on any clean checkout the TU is never compiled
# and the link dies with undefined references to meshtastic_build_version /
# meshtastic_build_epoch. PlatformIO says as much itself - BuildSources() raises "The
# main program is already constructed ... Please use env.BuildLibrary(...) or PRE-type
# script instead."
PRE_SCRIPT_PREFIX = "pre:"
POST_SCRIPT_PREFIX = "post:"
EXTRA_SCRIPT_PREFIXES = (PRE_SCRIPT_PREFIX, POST_SCRIPT_PREFIX)
# Basename of the generated translation unit, and the bin/build_info.py helpers that
# produce it. A script counts as the generator if it names the file or calls either
# helper, so the check keys off *what a script does* rather than its filename and keeps
# working if the build scripts are renamed.
GENERATED_TU_NAME = "build_info.cpp"
BUILD_INFO_GENERATOR_CALLS = ("render_build_info_cpp", "write_build_info_cpp")


def split_extra_script(entry):
    """Split an ``extra_scripts`` entry into its ``(prefix, path)`` parts.

    An entry with no recognised prefix gets ``""``; PlatformIO treats that as POST,
    which is exactly the failure mode these tests exist to catch.

    Args:
        entry: One verbatim ``extra_scripts`` value, e.g. ``"pre:bin/foo.py"``.

    Returns:
        A ``(prefix, relative_path)`` tuple.
    """
    for prefix in EXTRA_SCRIPT_PREFIXES:
        if entry.startswith(prefix):
            return prefix, entry[len(prefix) :].strip()
    return "", entry


def parse_extra_scripts(root=REPO_ROOT):
    """Return the top-level ``[env] extra_scripts`` entries, prefixes intact.

    Hand-parsed rather than read through configparser so the multi-line, tab-indented
    PlatformIO value and its ``;`` comments are handled exactly as written. Only the
    root ``platformio.ini`` is parsed: every variant that touches ``extra_scripts``
    interpolates ``${env.extra_scripts}``, so this list is the single source of truth.

    Args:
        root: Directory to treat as the repository root.

    Returns:
        A ``list[str]`` of entries in declaration order; empty if none are declared.
    """
    leaders = COMMENT_LEADERS_BY_SUFFIX[".ini"]
    entries = []
    collecting = False
    with open(os.path.join(root, PLATFORMIO_INI_NAME), errors="replace") as f:
        for raw in f:
            indented = raw[:1] in (" ", "\t")
            code = _strip_comment(raw, leaders).strip()
            if collecting and indented:
                if code:
                    entries.append(code)
                continue
            collecting = False
            if not indented and re.match(r"extra_scripts\s*=", code):
                collecting = True
                head = code.split("=", 1)[1].strip()
                if head:
                    entries.append(head)
    return entries


def script_generates_build_info(path):
    """Whether the script at ``path`` generates ``src/build_info.cpp``.

    Comments are stripped before matching (same reasoning as the volatile-macro scan):
    prose that merely *mentions* the generated TU - for instance a note in the post
    script explaining that generation moved to the pre script - is documentation, not
    generation, and must not be mistaken for it.

    Args:
        path: Absolute path to a candidate extra script.

    Returns:
        ``True`` if the script writes/renders the generated translation unit.
    """
    if not os.path.isfile(path):
        return False
    leaders = COMMENT_LEADERS_BY_SUFFIX.get(os.path.splitext(path)[1], DEFAULT_COMMENT_LEADERS)
    with open(path, errors="replace") as f:
        for raw in f:
            code = _strip_comment(raw, leaders)
            if GENERATED_TU_NAME in code:
                return True
            if any(call + "(" in code for call in BUILD_INFO_GENERATOR_CALLS):
                return True
    return False


def build_info_generator_entries(root=REPO_ROOT):
    """Return ``(entry, prefix)`` for every extra script that generates the TU.

    Args:
        root: Directory to treat as the repository root.

    Returns:
        A ``list[(entry, prefix)]``; empty when nothing generates the TU at all,
        which is itself a failure (the symbols would never be defined).
    """
    found = []
    for entry in parse_extra_scripts(root):
        prefix, rel = split_extra_script(entry)
        if script_generates_build_info(os.path.join(root, rel)):
            found.append((entry, prefix))
    return found


def test_extra_scripts_entries_resolve_to_real_files():
    """Parser sanity: every parsed entry must name a script that exists.

    Without this, a silently-broken parse would make the ordering guard below vacuous -
    it would find no generators and could then only fail on the "nothing generates it"
    branch, never on the ordering one it exists to police.
    """
    entries = parse_extra_scripts()
    assert entries, f"no extra_scripts parsed out of {PLATFORMIO_INI_NAME}"
    for entry in entries:
        _, rel = split_extra_script(entry)
        path = os.path.join(REPO_ROOT, rel)
        assert os.path.isfile(path), f"extra_scripts entry {entry!r} does not exist at {path}"


def test_build_info_generator_runs_before_platformio_globs_src():
    """The generator of src/build_info.cpp must be registered as a `pre:` extra script.

    This is an ordering invariant, not a style preference: the generated TU is
    gitignored, so on a clean checkout it does not exist when PlatformIO eagerly globs
    $PROJECT_SRC_DIR. A generator registered unprefixed (= POST) or `post:` creates the
    file after that glob has already been taken, so it is never compiled and the link
    fails with undefined references to meshtastic_build_version /
    meshtastic_build_epoch. See the block comment above for the exact PlatformIO
    call order.
    """
    generators = build_info_generator_entries()
    assert generators, (
        f"no extra_script in {PLATFORMIO_INI_NAME} generates src/{GENERATED_TU_NAME}: "
        "the build-identity symbols would never be defined"
    )
    late = [entry for entry, prefix in generators if prefix != PRE_SCRIPT_PREFIX]
    assert not late, (
        f"these extra_scripts generate src/{GENERATED_TU_NAME} but are not registered "
        f"with the {PRE_SCRIPT_PREFIX!r} prefix: {late}. They run AFTER PlatformIO has "
        "already globbed src/, so on a clean checkout (the TU is gitignored) the file "
        "is written too late to be compiled and the link fails with undefined "
        "references to meshtastic_build_version / meshtastic_build_epoch."
    )


# A minimal repo root for driving the ordering check: an ini that registers one
# generator script, plus the script itself. The {prefix} slot is what the mutation
# test varies.
SCRATCH_INI_TEMPLATE = (
    "[env]\n"
    "extra_scripts =\n"
    "\tpre:bin/innocent.py\n"
    "\t{prefix}bin/generator.py\n"
    "\tpost:bin/late.py\n"
    "; a trailing comment ends the value\n"
    "build_flags = -DFOO=1\n"
)
SCRATCH_GENERATOR_SRC = 'write_build_info_cpp(env["PROJECT_DIR"], ver, epoch)\n'
SCRATCH_INNOCENT_SRC = "# nothing to do with build_info.cpp generation\nprint('hi')\n"


def _scratch_ordering_root(prefix):
    """Build a throwaway repo root whose generator carries ``prefix``.

    mkdtemp() yields a unique directory per call, so concurrent copies of these tests
    never share a scratch tree. Returns the tmp root; the caller removes it.
    """
    root = tempfile.mkdtemp(prefix=f"extra-scripts-{os.getpid()}-")
    _plant(root, PLATFORMIO_INI_NAME, SCRATCH_INI_TEMPLATE.format(prefix=prefix))
    _plant(root, "bin/generator.py", SCRATCH_GENERATOR_SRC)
    _plant(root, "bin/innocent.py", SCRATCH_INNOCENT_SRC)
    _plant(root, "bin/late.py", SCRATCH_INNOCENT_SRC)
    return root


def test_ordering_check_flags_a_generator_that_is_not_a_pre_script():
    """Mutation check: a POST-registered generator must be detected, for both spellings.

    An unprefixed entry and an explicit `post:` entry are the same thing to PlatformIO,
    and both are wrong here. A guard that cannot go red is worthless.
    """
    for prefix in ("", POST_SCRIPT_PREFIX):
        root = _scratch_ordering_root(prefix)
        try:
            found = build_info_generator_entries(root)
            assert found == [(prefix + "bin/generator.py", prefix)], (
                f"the {prefix or 'unprefixed'} generator was not detected: {found}"
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_ordering_check_accepts_a_pre_registered_generator():
    """The same tree with the generator moved to `pre:` must pass the ordering rule."""
    root = _scratch_ordering_root(PRE_SCRIPT_PREFIX)
    try:
        found = build_info_generator_entries(root)
        assert found == [
            (PRE_SCRIPT_PREFIX + "bin/generator.py", PRE_SCRIPT_PREFIX)
        ], f"the pre-registered generator was not recognised: {found}"
        late = [entry for entry, prefix in found if prefix != PRE_SCRIPT_PREFIX]
        assert not late, f"a pre-registered generator was reported as late: {late}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- One definition of the build epoch, shared by every consumer -------------------
# The epoch lands in two independent artifacts: the generated TU (written by the pre
# script) and the build manifest's "build_epoch" field (written by the post script).
# Two inline copies of `datetime.now().replace(hour=0, ...)` would be two definitions
# that can drift - and, across a local-midnight rollover, actually disagree within one
# build. Everything must go through build_info.compute_build_epoch().
INLINE_EPOCH_COMPUTATION = "replace(hour=0"
BUILD_EPOCH_HELPER = "compute_build_epoch"


def test_build_epoch_helper_exists_and_is_midnight_local():
    """build_info.compute_build_epoch() returns midnight (local) on the build day."""
    assert hasattr(build_info, BUILD_EPOCH_HELPER), (
        f"bin/build_info.py defines no {BUILD_EPOCH_HELPER}(): the build epoch has no "
        "shared definition, so the generated TU and the build manifest each need their "
        "own copy of the computation"
    )
    epoch = build_info.compute_build_epoch()
    when = datetime.datetime.fromtimestamp(epoch)
    assert (when.hour, when.minute, when.second, when.microsecond) == (0, 0, 0, 0), (
        f"compute_build_epoch() returned {epoch} = {when}, which is not local midnight"
    )


def test_build_epoch_is_stable_across_calls_within_one_build():
    """Repeated calls must agree, so the TU and the manifest can never disagree.

    Both consumers run inside the same `pio run` process but at different points in it
    (pre script vs. post script). If the helper recomputed from the wall clock each
    time, a build spanning local midnight would bake one epoch into the TU and write a
    different one into the manifest.
    """
    assert hasattr(build_info, BUILD_EPOCH_HELPER), f"no {BUILD_EPOCH_HELPER}()"
    assert build_info.compute_build_epoch() == build_info.compute_build_epoch()


def test_no_extra_script_computes_the_build_epoch_inline():
    """Every registered extra script must take the epoch from the shared helper."""
    offenders = []
    for entry in parse_extra_scripts():
        _, rel = split_extra_script(entry)
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as f:
            if INLINE_EPOCH_COMPUTATION in f.read():
                offenders.append(rel)
    assert not offenders, (
        f"these extra scripts compute the build epoch inline instead of calling "
        f"build_info.{BUILD_EPOCH_HELPER}(): {offenders}. A second copy of the "
        "computation can drift from the generated TU's value."
    )


# --- No build-epoch "presence marker" macro may exist ------------------------------
# src/build_info.h briefly defined MESHTASTIC_HAS_BUILD_EPOCH as 1, and the call sites
# that used to say `#ifdef BUILD_EPOCH` were mechanically converted to
# `#if MESHTASTIC_HAS_BUILD_EPOCH` with their old `#else` fallback left in place. Both
# halves of that are wrong now that the epoch is an extern symbol in the generated TU:
#
#   * The marker is unconditionally 1 for anyone who includes the header, so every
#     `#else` branch behind it is unreachable dead code.
#   * The `#if` spelling fails silently in the worst possible direction. A translation
#     unit that uses the epoch but forgets to `#include "build_info.h"` sees the marker
#     undefined, `#if` evaluates the undefined identifier as 0, and the compiler quietly
#     selects the stale fallback - InkHUD's Applet.cpp fell back to a hardcoded
#     1738368000 (Feb 1 2025) - instead of erroring. The guard's only real effect was to
#     mask a missing include.
#
# Reading the extern symbols unguarded turns that same mistake back into a loud compile
# error on an undeclared identifier, which is the behaviour we want. So the marker must
# not exist anywhere in the C/C++ sources, in any spelling.
PRESENCE_MARKER_MACRO = "MESHTASTIC_HAS_BUILD_EPOCH"
# Every C/C++ surface the marker could hide in. variants/ is included alongside src/
# even though the marker only ever lived in src/: board variants are compiled into the
# same translation units, so a re-introduction there would be just as broken and just
# as invisible.
SOURCE_SCAN_GLOBS = (
    "src/**/*.c",
    "src/**/*.cpp",
    "src/**/*.h",
    "src/**/*.hpp",
    "src/**/*.ino",
    "variants/**/*.c",
    "variants/**/*.cpp",
    "variants/**/*.h",
    "variants/**/*.hpp",
)
BUILD_INFO_HEADER_RELPATH = "src/build_info.h"
# The stable symbols the header must keep declaring. Asserted alongside the marker's
# absence so that deleting or gutting build_info.h can never read as a passing fix.
BUILD_INFO_EXTERNS = (
    "meshtastic_build_version",
    "meshtastic_build_epoch_str",
    "meshtastic_build_epoch",
)


def scan_sources_for_presence_marker(root=REPO_ROOT):
    """Find every mention of the build-epoch presence marker in the C/C++ sources.

    Deliberately a plain substring scan with no comment stripping, unlike
    :func:`scan_build_surfaces_for_volatile_macros`. That scan tolerates commented-out
    text because a disabled compile flag is genuinely harmless. Here the macro is being
    abolished outright, so a commented-out `#if` or a doc comment still describing a
    marker that no longer exists is also a defect: it invites the guard back.

    ``root`` is injectable so the guard can be pointed at a throwaway tree and proven
    capable of going red (a guard that cannot fail is worthless).

    Args:
        root: Directory to treat as the repository root. Defaults to this checkout.

    Returns:
        A sorted ``list[(relpath, lineno, stripped_line)]`` of hits; empty when the
        marker is gone from every source file.
    """
    root_path = pathlib.Path(root)
    hits = []
    seen = set()
    for glob in SOURCE_SCAN_GLOBS:
        for path in sorted(root_path.glob(glob)):
            if not path.is_file():
                continue
            rel = path.relative_to(root_path).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            with open(path, errors="replace") as f:
                for lineno, raw in enumerate(f, 1):
                    if PRESENCE_MARKER_MACRO in raw:
                        hits.append((rel, lineno, raw.strip()))
    return sorted(hits)


def test_no_source_branches_on_the_build_epoch_presence_marker():
    """Guard (issue #8): no source may mention MESHTASTIC_HAS_BUILD_EPOCH.

    See the block comment above for why the marker is not merely redundant but actively
    harmful: it converts "you forgot the include" from a compile error into a silently
    wrong fallback value.
    """
    hits = scan_sources_for_presence_marker()
    detail = "\n".join(f"  {rel}:{line}: {text}" for rel, line, text in hits)
    assert not hits, (
        f"{PRESENCE_MARKER_MACRO} is still present. It is unconditionally 1 wherever "
        f"{BUILD_INFO_HEADER_RELPATH} is included, so every #else fallback behind it is "
        "unreachable, and any TU that forgets the include silently compiles the stale "
        "fallback instead of failing to build. Delete the guard scaffolding and read "
        f"the extern symbols directly:\n{detail}"
    )


def test_build_info_header_keeps_the_externs_and_defines_no_presence_marker():
    """The header must still declare the stable symbols, and define no marker.

    Two assertions in one test on purpose: "the marker is gone" is only the right
    outcome if the extern declarations that replace it are still there. Checked
    separately from the repo-wide scan so a re-introduced ``#define`` names the header
    directly instead of arriving as one line among many.
    """
    with open(os.path.join(REPO_ROOT, BUILD_INFO_HEADER_RELPATH)) as f:
        header = f.read()
    for symbol in BUILD_INFO_EXTERNS:
        assert symbol in header, (
            f"{BUILD_INFO_HEADER_RELPATH} no longer declares {symbol}: the call sites "
            "that read it unguarded would all fail to compile"
        )
    assert not re.search(r"#\s*define\s+" + PRESENCE_MARKER_MACRO, header), (
        f"{BUILD_INFO_HEADER_RELPATH} defines {PRESENCE_MARKER_MACRO}. The epoch symbol "
        "is always available to anyone who includes this header, so a presence marker "
        "can only ever be 1 here and 0 (silently, wrongly) in a TU that forgot the "
        "include"
    )


def _scratch_presence_marker_root():
    """A throwaway repo root for driving scan_sources_for_presence_marker().

    mkdtemp() already guarantees a unique directory per call; the pid and nanosecond
    stamp in the prefix are belt-and-braces, and make it obvious from a stray leftover
    directory which process and run created it. Either way, concurrent copies of these
    tests never share a scratch tree (same parallel-safety reasoning as
    _scratch_runner_root). Returns the tmp root; the caller removes it.
    """
    return tempfile.mkdtemp(prefix=f"presence-marker-{os.getpid()}-{time.time_ns()}-")


# One planted violation per spelling the preprocessor accepts, so the scan is proven to
# catch a re-introduction however it is written - not just the `#if` form used before.
PLANTED_MARKER_USES = {
    "src/planted/if_guard.cpp": (
        f"#if {PRESENCE_MARKER_MACRO}\n    useEpoch();\n#else\n    stale();\n#endif\n"
    ),
    "src/planted/ifdef_guard.h": f"#ifdef {PRESENCE_MARKER_MACRO}\nint guarded;\n#endif\n",
    "src/planted/defined_guard.hpp": f"#if defined({PRESENCE_MARKER_MACRO})\n#endif\n",
    "src/planted/negated_guard.c": f"#if !{PRESENCE_MARKER_MACRO}\nint fallback;\n#endif\n",
    "src/planted/ifndef_guard.cpp": f"#ifndef {PRESENCE_MARKER_MACRO}\n#endif\n",
    "src/planted/definition.h": f"#define {PRESENCE_MARKER_MACRO} 1\n",
    "src/planted/prose.ino": f"// fall back when {PRESENCE_MARKER_MACRO} is undefined\n",
    "variants/planted/variant.h": f"#if {PRESENCE_MARKER_MACRO}\n#endif\n",
}


def test_presence_marker_scan_flags_every_planted_spelling():
    """Mutation check: the guard must actually go red on a re-introduction.

    Plants the marker once per preprocessor spelling (and once under variants/) in a
    throwaway root and requires every one of them to be reported.
    """
    root = _scratch_presence_marker_root()
    try:
        for relpath, text in PLANTED_MARKER_USES.items():
            _plant(root, relpath, text)
        flagged = {rel for rel, _, _ in scan_sources_for_presence_marker(root)}
        missed = sorted(set(PLANTED_MARKER_USES) - flagged)
        assert not missed, f"the scan did not reach these planted violations: {missed}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# The end state this change produces: the extern symbols read unguarded. None of it may
# be flagged, or the guard would forbid the very thing it is pushing people towards.
LEGITIMATE_EPOCH_USES = {
    "src/planted/consumer.cpp": (
        '#include "build_info.h"\n'
        "if (tv.tv_sec < meshtastic_build_epoch) {\n"
        "    return RTCSetResultInvalidTime;\n"
        "}\n"
        'LOG_INFO("%s built at %s", meshtastic_build_version, meshtastic_build_epoch_str);\n'
    ),
    "src/planted/neighbour.h": "#define MESHTASTIC_HAS_A_DIFFERENT_THING 1\n",
}


def test_presence_marker_scan_ignores_unguarded_extern_use():
    """The guard must not flag the unguarded extern reads it exists to encourage."""
    root = _scratch_presence_marker_root()
    try:
        for relpath, text in LEGITIMATE_EPOCH_USES.items():
            _plant(root, relpath, text)
        hits = scan_sources_for_presence_marker(root)
        assert not hits, f"the scan flagged legitimate unguarded epoch use: {hits}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
