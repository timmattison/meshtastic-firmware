#pragma once

#include <stdint.h>

// Stable build-identity header (GitHub issue #8).
//
// The volatile build identity -- the full version string (which embeds the git
// short SHA and therefore changes on every commit) and the build epoch (which
// changes every day at the midnight rollover) -- is DEFINED in the generated
// translation unit src/build_info.cpp, not here. Isolating those two values in
// one generated .cpp means a new commit or the daily epoch change recompiles
// only build_info.o plus a relink, instead of forcing every object in src/ to
// rebuild (which is what the old global -DAPP_VERSION / -DBUILD_EPOCH compile
// flags did, because SCons keys each object's freshness on its exact command
// line). Consumers must read these stable extern symbols instead of the old
// -DAPP_VERSION / -DBUILD_EPOCH macros.

// Full firmware version string, including the git short SHA (e.g.
// "2.8.0.abc1234"). Never empty.
extern const char *const meshtastic_build_version;

// Decimal string form of the build epoch, for compile-time-string call sites
// that need the epoch as text rather than a number.
extern const char *const meshtastic_build_epoch_str;

// Unix epoch seconds of midnight (local time) on the build day.
extern const uint32_t meshtastic_build_epoch;

// Presence marker replacing the old `#ifdef`-style build-epoch macro guards: the build
// script always generates build_info.cpp, so the epoch symbol above is always
// available to any translation unit that includes this header. A translation
// unit that does not include this header will see MESHTASTIC_HAS_BUILD_EPOCH
// undefined and can fall back accordingly.
#define MESHTASTIC_HAS_BUILD_EPOCH 1
