# A local MinIO server for NixOS / nix users -- the counterpart of
# `setup.sh --role minio`, which needs apt, sudo and systemd:
#
#   nix-shell minio.nix --run 'bash bench/scripts/minio_local.sh test'    # S3StorageTest.*, S3DBTest.*
#   nix-shell minio.nix --run 'bash bench/scripts/minio_local.sh start'   # leave it running
#
# MinIO, the object store the bench nodes run, is what the paper's numbers
# were measured against. nixpkgs flags it as abandoned upstream with open
# CVEs, so it is permitted here explicitly. minio_local.sh binds it to
# 127.0.0.1 only, with the default minioadmin credentials: a test fixture,
# not a service.
{ nixpkgs ? <nixpkgs> }:
let
  pkgs = import nixpkgs {
    config.allowInsecurePredicate = p: (p.pname or "") == "minio";
  };
in
pkgs.mkShell {
  packages = with pkgs; [
    minio
    minio-client
  ];
  shellHook = ''
    export OZONEDB_HOME="$PWD"
  '';
}
