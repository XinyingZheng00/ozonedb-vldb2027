cmake_minimum_required(VERSION 3.16)

# Dependencies come from vcpkg by default. vcpkg is NOT vendored:
# bench/scripts/setup.sh clones it at the builtin-baseline in vcpkg.json.
# Precedence: an explicitly supplied CMAKE_TOOLCHAIN_FILE (preset or -D) >
# $VCPKG_ROOT > a vcpkg/ checkout inside the repo (gitignored; what setup.sh
# creates). -DOZONEDB_USE_VCPKG=OFF skips all of that and takes the
# dependencies from the system (shell.nix, a distro with cmake config files
# for protobuf, gtest, boost, openssl and aws-sdk-cpp).
option(OZONEDB_USE_VCPKG "Resolve dependencies through the vcpkg toolchain" ON)
if (OZONEDB_USE_VCPKG AND NOT DEFINED CMAKE_TOOLCHAIN_FILE)
    if (DEFINED ENV{VCPKG_ROOT})
        set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake" CACHE STRING "vcpkg toolchain")
    elseif (EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vcpkg/scripts/buildsystems/vcpkg.cmake")
        set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_SOURCE_DIR}/vcpkg/scripts/buildsystems/vcpkg.cmake" CACHE STRING "vcpkg toolchain")
    else()
        message(FATAL_ERROR "No vcpkg toolchain found. Run `bash bench/scripts/setup.sh --role client`, export VCPKG_ROOT, pass -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake, or build with -DOZONEDB_USE_VCPKG=OFF against system packages")
    endif()
endif()

# Project Configuration
project(OzoneDB VERSION 1.0)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED True)
if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} -O3")

# Profiling build: `cmake -DOZONEDB_PROFILING=ON ..` adds frame pointers
# and line-table debug info so perf/flamegraph produces readable stacks
# without dropping Release optimizations.
option(OZONEDB_PROFILING "Build with frame pointers + debug info for perf/flamegraph" OFF)
if (OZONEDB_PROFILING)
    add_compile_options(-fno-omit-frame-pointer -g)
    add_link_options(-rdynamic)
endif()
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Optional backends
option(OZONEDB_ENABLE_CORFU "Build the CorfuDB shared-log backend (native C++ client, no JDK needed)" OFF)
option(OZONEDB_ENABLE_S3 "Build the S3/MinIO/R2 storage backend (requires aws-sdk-cpp)" ON)

# libozonedb.so, the shim the YCSB binding's jni.OzoneDBJNI loads. The only
# part of the build that needs a JDK.
option(OZONEDB_BUILD_JNI "Build libozonedb.so, the JNI shim the YCSB binding loads" ON)

# Find Packages
#
# No link_directories() here: vcpkg's imported targets already carry their
# own link information.
# protobuf >= 22 ships protobuf_generate_cpp only behind this switch.
set(protobuf_MODULE_COMPATIBLE ON)
find_package(Protobuf CONFIG REQUIRED)
find_package(GTest CONFIG REQUIRED)
# boost-uuid is header-only, so no COMPONENTS: a system Boost has no
# libboost_uuid to find. OpenSSL in module mode for the same reason -- only
# vcpkg ships a CMake config for it.
find_package(Boost 1.70 REQUIRED)
find_package(OpenSSL REQUIRED)

# The shim only needs jni.h, so it degrades to a skip rather than failing a
# build that never wanted a JDK in the first place.
if (OZONEDB_BUILD_JNI)
    find_package(JNI)
    if (NOT JNI_FOUND)
        message(STATUS "JNI not found; skipping the libozonedb.so shim. Pass -DOZONEDB_BUILD_JNI=OFF to silence this.")
        set(OZONEDB_BUILD_JNI OFF)
    endif()
endif()

if (OZONEDB_ENABLE_S3)
    find_package(AWSSDK CONFIG REQUIRED COMPONENTS s3)
endif()

# Protobuf Compilation
#
# Generated into the build tree, not src/include/ozonedb/protobuf/. Sources
# include these as "protobuf/record.pb.h", so PROTO_GEN_ROOT (not the
# protobuf/ subdir itself) is what goes on the include path.
set(PROTO_GEN_ROOT ${CMAKE_CURRENT_BINARY_DIR}/generated)
file(MAKE_DIRECTORY ${PROTO_GEN_ROOT}/protobuf)

# Remove the pre-move generation directory. It was CMake-generated output
# (gitignored, protoc-written), and because src/include/ozonedb comes ahead
# of PROTO_GEN_ROOT for other headers, a leftover copy there would silently
# shadow the freshly generated one on any machine that has built before.
if (EXISTS ${PROJECT_SOURCE_DIR}/src/include/ozonedb/protobuf)
    message(STATUS "Removing stale in-tree protobuf output: src/include/ozonedb/protobuf")
    file(REMOVE_RECURSE ${PROJECT_SOURCE_DIR}/src/include/ozonedb/protobuf)
endif()
protobuf_generate_cpp(
    PROTO_SRCS PROTO_HDRS
    ${PROJECT_SOURCE_DIR}/src/include/ozonedb/record.proto
    ${PROJECT_SOURCE_DIR}/src/include/ozonedb/sstable.proto
    PROTOC_OUT_DIR ${PROTO_GEN_ROOT}/protobuf
)

# The CorfuDB wire protocol, vendored under src/corfu_proto (see its
# README). Generated into build/generated/corfu_proto/ keeping the
# service/ subdirectory, because the files import each other by that
# relative path; sources include "service/corfu_message.pb.h".
set(CORFU_PROTO_SRCS "")
set(CORFU_PROTO_HDRS "")
if (OZONEDB_ENABLE_CORFU)
    set(CORFU_PROTO_ROOT ${PROJECT_SOURCE_DIR}/src/corfu_proto)
    file(MAKE_DIRECTORY ${PROTO_GEN_ROOT}/corfu_proto/service)
    protobuf_generate_cpp(
        CORFU_PROTO_SRCS CORFU_PROTO_HDRS
        ${CORFU_PROTO_ROOT}/rpc_common.proto
        ${CORFU_PROTO_ROOT}/log_data.proto
        ${CORFU_PROTO_ROOT}/tx_resolution.proto
        ${CORFU_PROTO_ROOT}/server_errors.proto
        ${CORFU_PROTO_ROOT}/service/base.proto
        ${CORFU_PROTO_ROOT}/service/layout.proto
        ${CORFU_PROTO_ROOT}/service/sequencer.proto
        ${CORFU_PROTO_ROOT}/service/log_unit.proto
        ${CORFU_PROTO_ROOT}/service/corfu_message.proto
        IMPORT_DIRS ${CORFU_PROTO_ROOT}
        PROTOC_OUT_DIR ${PROTO_GEN_ROOT}/corfu_proto
    )
endif()

# Library Target
set(OZONEDB_SOURCES
    src/db/compaction.cpp
    src/db/db.cpp
    src/db/log_handler.cpp
    src/db/log_key_index.cpp
    src/db/cache.cpp
    src/db/metadata_log_handler.cpp
    src/db/task_log_handler.cpp
    src/db/checkpoint.cpp
    src/db/storage.cpp
    src/db/disk_cache_storage.cpp
    src/db/sstable/block_handler.cpp
    src/db/sstable/table_builder.cpp
    src/db/sstable/iterator.cpp
    src/db/sstable/table_reader.cpp
    src/db/sstable/sstable_handler.cpp
    src/db/sstable/filter_block.cpp
    src/db/sstable/comparator.cpp
    src/db/sstable/bloom.cpp
    src/util/read_json.cpp
    src/util/protobuf_serializer.cpp
    src/util/helper.cpp
    ${PROTO_SRCS} ${PROTO_HDRS}
)
if (OZONEDB_ENABLE_CORFU)
    list(APPEND OZONEDB_SOURCES
        src/db/corfu_storage.cpp
        src/db/log_trimmer.cpp
        src/db/corfu/corfu_codec.cpp
        src/db/corfu/corfu_transport.cpp
        src/db/corfu/corfu_layout.cpp
        src/db/corfu/corfu_rpc.cpp
        src/db/corfu/corfu_reader.cpp
        src/db/corfu/native_corfu_client.cpp
        ${CORFU_PROTO_SRCS}
    )
endif()
if (OZONEDB_ENABLE_S3)
    list(APPEND OZONEDB_SOURCES src/db/s3_storage.cpp)
endif()

add_library(OzoneDB SHARED ${OZONEDB_SOURCES})

# PUBLIC so every consumer -- runUnitTests, the corfu smoke binaries, and the
# JNI shim -- inherits these instead of restating them. Public headers such as
# db.h include "protobuf/record.pb.h", so PROTO_GEN_ROOT must be public too.
# PROTO_GEN_ROOT deliberately comes first: it must win over any stale
# src/include/ozonedb/protobuf/ left by a pre-move build.
target_include_directories(OzoneDB PUBLIC
    ${PROTO_GEN_ROOT}
    ${PROJECT_SOURCE_DIR}/src/include/ozonedb
    ${PROJECT_SOURCE_DIR}/src/util
    ${Boost_INCLUDE_DIRS}
)

target_link_libraries(OzoneDB
    protobuf::libprotobuf
    ${Boost_LIBRARIES}
    OpenSSL::SSL OpenSSL::Crypto
    pthread
)

if (OZONEDB_ENABLE_S3)
    target_compile_definitions(OzoneDB PUBLIC OZONEDB_ENABLE_S3)
    target_link_libraries(OzoneDB ${AWSSDK_LINK_LIBRARIES})
endif()

if (OZONEDB_ENABLE_CORFU)
    # The generated Corfu protos are included as "service/xxx.pb.h".
    target_compile_definitions(OzoneDB PUBLIC OZONEDB_ENABLE_CORFU)
    target_include_directories(OzoneDB PUBLIC ${PROTO_GEN_ROOT}/corfu_proto)
endif()

# JNI shim -> libozonedb.so
#
# The $ORIGIN rpath lets libozonedb.so find libOzoneDB.so sitting beside it in
# the build directory, so neither has to be copied into /usr/lib. OUTPUT_NAME
# differs from the OzoneDB target only by case, which is fine on the Linux
# bench nodes this targets but would collide on a case-insensitive filesystem.
if (OZONEDB_BUILD_JNI)
    add_library(ozonedb_jni SHARED src/jni/jni_OzoneDBJNI.cpp)
    set_target_properties(ozonedb_jni PROPERTIES
        OUTPUT_NAME ozonedb
        BUILD_WITH_INSTALL_RPATH TRUE
        INSTALL_RPATH "$ORIGIN"
    )
    target_include_directories(ozonedb_jni PRIVATE
        ${JNI_INCLUDE_DIRS}
        ${PROJECT_SOURCE_DIR}/src/jni
    )
    # OzoneDB only -- the shim implements Java_jni_* entry points and uses the
    # JNIEnv it is handed, so it must not link libjvm itself.
    target_link_libraries(ozonedb_jni PRIVATE OzoneDB)
endif()

# Unit tests. CorfuStorageTest.* self-skip without CORFU_TEST_ENDPOINT, so
# `ctest` is green on a box with no Corfu server.
set(OZONEDB_TEST_SOURCES
    tests/test_all.cpp
    tests/test_tool.cpp
    tests/test_db.cpp
    tests/test_storage.cpp
    tests/test_protobuf.cpp
    tests/test_sstable.cpp
    tests/test_filter_block.cpp
    tests/test_checkpoint.cpp
    tests/test_cache.cpp
    tests/test_disk_cache_storage.cpp
    tests/test_frequency_sketch.cpp
)
if (OZONEDB_ENABLE_CORFU)
    list(APPEND OZONEDB_TEST_SOURCES
        tests/test_corfu_storage.cpp
        tests/test_corfu_codec.cpp
    )
endif()
add_executable(runUnitTests ${OZONEDB_TEST_SOURCES})
# test_all.cpp defines main, so gtest_main is not linked.
target_link_libraries(runUnitTests OzoneDB GTest::gtest GTest::gmock)

if (OZONEDB_ENABLE_CORFU)
    add_executable(corfu_smoke tests/corfu_smoke.cpp)
    target_link_libraries(corfu_smoke OzoneDB)

    add_executable(corfu_multiwriter_smoke tests/corfu_multiwriter_smoke.cpp)
    target_link_libraries(corfu_multiwriter_smoke OzoneDB)

    # Read-only stream diagnostic (dropped-above-seal appends, metadata.log
    # rollforward simulation). See tests/corfu_stream_stats.cpp.
    add_executable(corfu_stream_stats tests/corfu_stream_stats.cpp)
    target_link_libraries(corfu_stream_stats OzoneDB)

    # Probe of the native client: layout, tails, trim mark and a histogram
    # of the log by DataType and stream membership. See
    # tests/corfu_native_probe.cpp.
    add_executable(corfu_native_probe tests/corfu_native_probe.cpp)
    target_link_libraries(corfu_native_probe OzoneDB)
endif()

# `ctest` runs the whole gtest binary from the build directory, which is
# where the tests expect to find ../src/config/. The tests write under
# OZONEDB_TEST_ROOT (default /tank/test/, see tests/test_env.h).
enable_testing()
add_test(NAME runUnitTests COMMAND runUnitTests WORKING_DIRECTORY ${CMAKE_BINARY_DIR})

# Custom Clean Targets, scoped to the ozonedb directory under the data root.
add_custom_target(clean-db
    COMMAND rm -rf /tank/db/ozonedb
)
