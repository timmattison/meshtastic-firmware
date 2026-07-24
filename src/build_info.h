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

// Read these symbols unguarded. There is deliberately no "do we have a build epoch?"
// feature macro to wrap them in, because such a macro cannot be anything but harmful
// here: the build script always generates build_info.cpp, so it could only ever be true
// for a translation unit that includes this header, while a translation unit that
// forgot the include would see it undefined, have `#if` silently evaluate it as 0, and
// quietly compile a stale hardcoded fallback instead of failing. Without a guard, that
// same mistake is a loud compile error on an undeclared identifier -- which is the
// behaviour we want, and the reason the old `#ifdef BUILD_EPOCH` guards are gone.
