# Changelog

## 0.2.0 — 2026-10-29

- Added `stream_samples`: ordered delivery of verified samples with one background
  download worker and configurable bounded prefetch.
- Added a dedicated disk cache with fixed-commit size admission, LRU eviction,
  sample leases, exclusive ownership and completed-entry offline reuse.
- Added `download_sample(..., storage='direct')` and CLI `--storage direct` to
  install verified assets without a second full-size Hub-cache copy.
- Reuse completed metadata when its download receipt matches the pinned source
  and local hash; repeat downloads can avoid all asset requests.
- Added streaming examples, lifetime/cache documentation and scheduling/integrity
  tests. Version 0.1's default shared Hub-cache download mode remains available.

## 0.1.0 — 2026-10-29

- Initial catalog search, pinned-version downloads, SHA256 verification, AnnData
  access, coordinate helpers, CLI and bilingual user guides.
