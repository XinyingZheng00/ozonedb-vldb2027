# Build environment for NixOS / nix users, without vcpkg:
#
#   nix-shell --run 'cmake -B build -G Ninja -DOZONEDB_USE_VCPKG=OFF -DOZONEDB_ENABLE_CORFU=ON && cmake --build build'
#
# Every dependency of vcpkg.json comes from nixpkgs instead. The JDK and
# Maven are for the JNI shim and the YCSB binding.
{ pkgs ? import <nixpkgs> { } }:
let
  awsSdk = pkgs.aws-sdk-cpp.override {
    apis = [ "s3" ];
    customMemoryManagement = false;
  };
in
pkgs.mkShell {
  packages = with pkgs; [
    cmake
    ninja
    gcc
    pkg-config
    git
    protobuf
    gtest
    boost
    openssl
    zlib
    awsSdk
    jdk17
    maven
    python3
    python3Packages.pyyaml
  ];
  JAVA_HOME = pkgs.jdk17.home;
  shellHook = ''
    export OZONEDB_HOME="$PWD"
  '';
}
