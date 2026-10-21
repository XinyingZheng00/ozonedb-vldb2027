#!/usr/bin/env bash
#
# Build everything a YCSB run needs: libOzoneDB.so, libozonedb.so (the JNI
# shim), runUnitTests, the Corfu smoke binaries, and the YCSB ozonedb binding.
#
# Run this after any C++ change that a YCSB run must see. Nothing is installed
# outside the repo: libozonedb.so carries an $ORIGIN rpath so it finds
# libOzoneDB.so beside it in build/, and jni.OzoneDBJNI resolves the shim via
# $OZONEDB_HOME/build. That means no sudo, and no stale copy in /usr/lib
# silently winning over a fresh build.

set -euo pipefail

: "${OZONEDB_HOME:?OZONEDB_HOME must be set (see bench/scripts/setup.sh)}"

JOBS="$(nproc 2>/dev/null || echo 4)"

# /tank is where the bench configs put db_path.
sudo mkdir -p /tank && sudo chmod 777 /tank

cmake -B "$OZONEDB_HOME/build" -S "$OZONEDB_HOME" -DOZONEDB_ENABLE_CORFU=ON
cmake --build "$OZONEDB_HOME/build" -j"$JOBS"

cd "$OZONEDB_HOME/ycsb"
mvn -q -pl site.ycsb:ozonedb-binding -am package -DskipTests

echo
echo "built:"
echo "  $OZONEDB_HOME/build/libOzoneDB.so"
echo "  $OZONEDB_HOME/build/libozonedb.so"
echo "  $OZONEDB_HOME/build/runUnitTests   (cd build && ctest)"
