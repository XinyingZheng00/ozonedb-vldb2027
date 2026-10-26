#!/usr/bin/env bash
#
# Build everything a YCSB run needs: libOzoneDB.so, libozonedb.so (the JNI
# shim), runUnitTests, the Corfu smoke binaries, and the YCSB ozonedb binding.
#
# Run this after any C++ change that a YCSB run must see. Nothing is installed
# outside the repo: libozonedb.so has an $ORIGIN rpath to libOzoneDB.so in
# build/, and jni.OzoneDBJNI loads the shim from $OZONEDB_HOME/build.

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
