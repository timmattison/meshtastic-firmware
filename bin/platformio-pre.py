#!/usr/bin/env python3
# trunk-ignore-all(ruff/F821)
# trunk-ignore-all(flake8/F821): For SConstruct imports
from build_info import compute_build_epoch, write_build_info_cpp
from readprops import readProps

Import("env")
platform = env.PioPlatform()

# Read the version once, for every platform. Both consumers below need it: the
# generated translation unit needs it on ALL platforms (portduino/native reads
# meshtastic_build_version in src/platform/portduino/PortduinoGlue.cpp), while
# only the embedded targets name their artifacts after it.
prefsLoc = env["PROJECT_DIR"] + "/version.properties"
verObj = readProps(prefsLoc)

# Generate src/build_info.cpp - the single translation unit that carries the volatile
# build identity (git SHA + build epoch) - issue #8.
#
# THIS MUST HAPPEN IN A `pre:` SCRIPT. PlatformIO's builder/main.py runs the `pre:`
# extra scripts, then $BUILD_SCRIPT (whose BuildSources() eagerly globs
# $PROJECT_SRC_DIR into a concrete file list), then the `post:` extra scripts - and an
# *unprefixed* extra_scripts entry is a POST script. src/build_info.cpp is gitignored
# because its content changes every commit, so a generator running after the glob would
# create it too late to be compiled on any clean checkout and the link would fail with
# undefined references to meshtastic_build_version / meshtastic_build_epoch.
# write_build_info_cpp() only rewrites the file when its content actually changes, so an
# unchanged SHA/epoch does not bump the mtime and does not trigger a needless recompile.
write_build_info_cpp(env["PROJECT_DIR"], verObj["long"], compute_build_epoch())

if platform.name == "native":
    env.Replace(PROGNAME="meshtasticd")
else:
    env.Replace(PROGNAME=f"firmware-{env.get('PIOENV')}-{verObj['long']}")
    env.Replace(ESP32_FS_IMAGE_NAME=f"littlefs-{env.get('PIOENV')}-{verObj['long']}")

# Print the new program name for verification
print(f"PROGNAME: {env.get('PROGNAME')}")
if platform.name == "espressif32":
    print(f"ESP32_FS_IMAGE_NAME: {env.get('ESP32_FS_IMAGE_NAME')}")
