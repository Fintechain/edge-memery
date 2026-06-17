# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.12] - 2026-06-18

### Fixed

- Hardened data writes so old SQLite databases are migrated before insert.
- Made `write_memory` and `write_memories_batch` tolerate plain strings, bad JSON fields, and unknown `hall_id` values.
- Normalized blank `memory_type`, `title`, tags, source files, and numeric scores before SQLite writes.
- Returned clean empty-content errors instead of leaking SQLite `NOT NULL` failures.
- Prevented vector index failures from blocking durable SQLite writes.

## [1.11] - 2026-06-17

### Added

- Added `memery doctor` for user-friendly installation diagnostics.
- Added robust MCP stdio handling that ignores blank terminal input.

### Fixed

- Fixed LanceDB startup when an existing `memories.lance` table is present.
- Fixed direct source-tree startup so it does not depend on the clone directory name.

### Removed

- Removed obsolete duplicate vector backend files and local runtime artifacts from the clean release.

### Added

- Standard Python packaging with an installable `memery` command.
- Apache License 2.0, security policy, contribution guide, disclaimer, and GitHub templates.
- User-profile runtime storage under `~/.memery` by default.

## [1.10] - 2026-06-16

### Added

- Pinned project core, latest-conversation summary, and current project summary.
- Generalized software, research, business, learning, and general contexts.
- Revision-aware context caching and bounded summary rebuilding.
- Thread-local SQLite connections, WAL tuning, composite indexes, and batch writes.
- Project-prefiltered LanceDB retrieval and repeatable stress benchmarks.
- Temporal knowledge graph, code graph analysis, task snapshots, and decisions.
