#!/usr/bin/env bash
#
# pio.sh — PlatformIO wrapper that isolates the PlatformIO core directory
# (toolchains, frameworks, platforms, downloaded package cache) per git
# worktree, then forwards every argument to the real `pio`.
#
# Why this exists:
#   Meshtastic worktrees pin different ESP32 platforms. The 2.7.0 branch uses
#   the official espressif32@6.11.0 (Arduino-ESP32 core 2.0.17); develop uses
#   pioarduino (Arduino-ESP32 core 3.x). BOTH platforms install a package
#   literally named "framework-arduinoespressif32" into the *shared*
#   ~/.platformio cache, at incompatible versions. Whichever you build last
#   wins that folder, so the next build against the other platform can no
#   longer find a package matching its required version and dies with:
#
#       TypeError: expected str, bytes or os.PathLike object, not NoneType
#         ... arduino.py: join(PioPlatform().get_package_dir(
#             "framework-arduinoespressif32"), "tools", "platformio-build.py")
#
#   (get_package_dir() returns None, and os.path.join(None, ...) throws.)
#
#   Giving each worktree its own core dir means the two platform lines keep
#   their own copies of the framework and never clobber each other again. The
#   cache lives at <worktree>/.platformio, which .gitignore already ignores.
#   The one-time cost is re-downloading the toolchain into each worktree.
#
# Usage:
#   ./pio.sh run -e t-deck-tft -t upload
#   ./pio.sh pkg install
#   ...anything you'd pass to `pio`.
#
set -euo pipefail

# The root of the worktree containing $PWD. `--show-toplevel` returns a
# different path per worktree, which is exactly the key we isolate on. It also
# means the cache is anchored to the worktree even when pio.sh is invoked from
# a subdirectory.
if ! worktree_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "pio.sh: not inside a git worktree — run it from your firmware checkout." >&2
  exit 1
fi

if ! command -v pio >/dev/null 2>&1; then
  echo "pio.sh: 'pio' was not found on PATH." >&2
  exit 127
fi

# PlatformIO creates this directory on first use if it does not exist.
export PLATFORMIO_CORE_DIR="${worktree_root}/.platformio"

echo "pio.sh: using isolated PLATFORMIO_CORE_DIR=${PLATFORMIO_CORE_DIR}" >&2

exec pio "$@"
