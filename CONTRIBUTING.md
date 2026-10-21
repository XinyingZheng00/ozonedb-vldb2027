# Contributing

OzoneKV is a research prototype. Issues and pull requests are welcome, with
these expectations.

## Before you open a pull request

1. Build with `-DOZONEDB_ENABLE_CORFU=ON` and run `ctest` from `build/`.
2. If you touch `src/db/corfu_storage.cpp` or `src/db/corfu/`, run
   `corfu_multiwriter_smoke` against a fresh Corfu server.
3. If you touch the read or write path, say so in the description. The paper's
   numbers come from the tag `vldb2027`, and a hot-path change moves them.
4. Follow `.clang-format` (Google style, east const, no wrapping).
5. Keep header comments that document lifetimes, lock order and aliasing up to
   date. `CLAUDE.md` is the architecture document; update it when the design
   changes.

## Reporting a problem

Open an issue with the config file, the command, and the `[corfu]` /
`[lru_cache]` lines the engine prints at close. `KNOWN-ISSUES.md` lists the
hazards we already know about.

## License

By contributing you agree that your contribution is licensed under the
Apache License 2.0 (see `LICENSE`).
