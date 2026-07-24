#!/usr/bin/env python3

"""RED STUB - intentionally wrong; the correct implementation lands in green (#8)."""

VOLATILE_MACROS = ("APP_VERSION", "BUILD_EPOCH")


def assemble_global_flags(version_short, pioenv, repo_owner, pref_flags=()):
    """WRONG: still injects the volatile macros onto the global flags."""
    return [
        "-DAPP_VERSION=" + version_short,
        "-DAPP_VERSION_SHORT=" + version_short,
        "-DAPP_ENV=" + pioenv,
        "-DAPP_REPO=" + repo_owner,
        "-DBUILD_EPOCH=0",
    ] + list(pref_flags)


def render_build_info_cpp(version_long, build_epoch):
    """WRONG: emits only the include, none of the baked values."""
    return '#include "build_info.h"\n'
