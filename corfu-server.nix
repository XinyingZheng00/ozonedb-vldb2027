# CorfuDB server environment for NixOS / nix users -- the counterpart of
# `setup.sh --role corfu-server`, which needs apt:
#
#   nix-shell corfu-server.nix --run 'bash bench/scripts/corfu_local.sh build'
#   nix-shell corfu-server.nix --run 'bash bench/scripts/corfu_local.sh test'
#
# CorfuDB's Maven build runs prebuilt protoc and protoc-gen-grpc-java
# binaries it downloads, which NixOS's stub loader refuses. So the JDK and
# Maven live in an FHS environment, `corfu-fhs` (`corfu-fhs` alone is a
# shell, `corfu-fhs script args...` runs a script), and corfu_local.sh
# re-executes itself inside it when it finds that on PATH.
#
# JDK 25 matches setup.sh's --corfu-jdk default: the pinned CorfuDB commit
# is built with it, and bin/corfu_server then runs on the same JDK.
{ pkgs ? import <nixpkgs> { } }:
let
  fhs = pkgs.buildFHSEnv {
    name = "corfu-fhs";
    targetPkgs = p: with p; [
      jdk25
      maven
      git
      which
      procps
      iproute2
      zlib
      stdenv.cc.cc.lib
    ];
    profile = ''
      export JAVA_HOME=${pkgs.jdk25.home}
      export CORFU_FHS=1
    '';
    runScript = "bash";
  };
in
pkgs.mkShell {
  packages = [ fhs ];
  shellHook = ''
    export OZONEDB_HOME="$PWD"
  '';
}
