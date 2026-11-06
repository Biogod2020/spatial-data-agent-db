# Changelog

## 0.4.1 — 2026-11-06

- New default release `v1.1.1-20261106` (aliases `metadata-v2`, `metadata-v2.2.1`, `v1.1`): the same paper data
  (`999566b`) with curated metadata **v2.2.1** (tag `metadata-v2.2.1-20261106`; public catalog `2511bb7`, gated
  `b90f301`). It carries corrections from a human review of 200 samples: life stage 51 samples, sex 77, disease
  terms 25, disease status 8, organ/UBERON 1. All nine `metadata/v2` SHA256s are recorded.
- New field `sex_source` (extracted field / sample text / manual / not stated). It is filterable
  (`sex_source="sample text"`, CLI `--where`), listed by `fields()` and shown in `card()["curated"]["donor"]`.
- `v1.1.0-20261105` (metadata v2.1, the 0.4.0 default) stays resolvable as `v1.1.0-20261105`,
  `metadata-v2.1` or `v1.1.0`. The aliases `metadata-v2` and `v1.1` now point to `v1.1.1-20261106`.
- Tables without `sex_source` (v2.1) load exactly as in 0.4.0.
- A hand-corrected field has a null `annotation_confidence` (since v2.2.1). `min_confidence` treats it as
  human-checked, so it passes instead of being dropped.
- `cite()` names the metadata tag of the release in use (`sdadb_metadata_v2_2_1_20261106`).

## 0.4.0 — 2026-11-05

Search and filter on curated sample metadata v2; the paper release stays exactly reproducible.

**Releases**

- New default release `v1.1.0-20261105` (aliases `metadata-v2`, `v1.1`). It has the same data commit and files
  as the paper release (`999566b`), plus the metadata v2 table (`metadata/v2/samples_metadata_v2.csv`, version
  2.1, tag `metadata-v2.1-20261105`, with one `assay_name` per assay). The table is read from the public catalog (`0ddfdbf`), or from the gated dataset
  (`1c0b636`) with `source="gated"`, and checked against its recorded SHA256.
- `paper` / `v1.0.0-20261025` is unchanged and has the v3 metadata only. A bare commit (`999566b…`) also opens
  the paper release. Saved selections reopen the release they were saved from.

**Metadata v2**

- Joined by `gsm`: `organ`, UBERON `tissue_ontology_id`/`_name`, `sample_kind`, `disease_status`, MONDO
  `disease_ontology_ids`/`_names`, `life_stage`, `sex`, `donor_key`, taxonomy (`organism_taxon_id`,
  `organism_common_name`, `organisms`, `feature_species`), `assay_name`, `assay_type`, `preservation`,
  `pubmed_ids`, the raw GEO texts, per-field confidence and `metadata_flags`.
- `platform` (204 corrected samples), `organism`, `sex`, `review_category` and `review_notes` take the v2 value.
  The v3 value stays in `<name>_v3`. Every other v3 field is kept.
- `annotation_min_confidence` (client-derived) is the lowest per-field confidence.
- QA on 300 random samples gave these wrong rates per field: organ 0.3%, tissue term 0.7%, sample kind 0.7%,
  life stage 1.7% and disease 3.0%. A study-level disease sometimes reaches an unmarked control (about 1.5%).

**Search**

- New filters in `Catalog.search`, the CLI and MCP: `organ`, `disease_status`, `sample_kind`, `life_stage`,
  `sex`, `assay_type`, `assay_name` and `preservation`. These are controlled values with typo suggestions and
  common aliases (`tumor`, `healthy`, `frozen`, `imaging`...).
- `disease` matches a MONDO ID, or a MONDO name exactly or as a case-insensitive substring. Parent terms are
  not expanded. `where="disease~TEXT"` still searches the GEO text.
- `organism` matches a scientific name, common name or taxon ID, against every listed host species.
- `min_confidence` (`--min-confidence`) and `exclude_flags` (`--exclude-flags [FLAGS]`) drop uncertain or
  flagged annotations.
- Keyword ranking also indexes organ, UBERON and MONDO names, sample kind, life stage, assay name and type,
  common name and preservation. The index of the paper release is unchanged.
- Compact rows and tables show organ, sample kind, disease status, MONDO names and assay name. `card()` has a
  `curated` section. JSON results and MCP results name the `metadata` tag.
- `cite()` adds an entry for the metadata v2 tag (commit and table SHA256) and the v2 PubMed IDs.

## 0.3.2 — 2026-11-03

- Starter pack: swapped 5 low-resolution Visium H&E samples (4.7–7.3 µm/px) for 5 at cell resolution
  (0.73–0.93 µm/px: human lung, colon, endometrium, liver; mouse kidney). Still 88 samples; public commit `ab5017a`.

## 0.3.1 — 2026-11-03

- Starter pack: 88 representative samples of the paper release, downloadable without login.
  - It covers 12 platforms and 20 organ groups, and 60 samples have an image. Files are byte-identical to the
    gated release and published in the public catalog repository (commit `68b3374`).
  - Use it with `sda-db starter`, `--release starter` or `open_catalog("starter")`.
- Releases can now be subsets of a release, bound by a SHA256-checked member list.
- `examples/starter_pack.ipynb`: a no-login Colab walkthrough covering listing, loading, gene overlay, an H&E
  patch and gene search.

## 0.3.0 — 2026-11-02

Agent-ready release: search without an account, ranked keyword search, cohort tooling, an MCP server and a
rewritten CLI.

**Releases**

- Added a named release registry, shipped with the package.
  - The default is now the paper release `v1.0.0-20261025` (commit `999566b`), which matches the dataset card.
    Version 0.2 pinned `8f116b8`, which has the same samples and files and is still available as `2026-10-29`.
  - `latest` resolves `main` to one commit.

**Search**

- `Catalog.open()` reads the catalog of the paper release from the ungated mirror. Each mirrored file is
  checked against the SHA256 recorded for the release, so searching needs no account. Sample downloads still
  use the gated dataset at the release commit.
- The catalog joins the release's atlas metadata: study title, summary, design and PubMed IDs; reported
  disease, condition, age, sex and donor; publication; and counts evidence.
- Keyword search is ranked with BM25 over sample fields and study text.
  - Every term must match; `match="any"`, phrases, exclusions and prefixes are supported.
  - Terms with no match in the release are reported.
  - "alzheimer" now finds 452 samples in 27 studies; it found 0 in 0.2.
- Filters accept any field.
  - Values ignore case, spaces and hyphens, and accept common names (`human`, `visium hd`) and lists (OR).
  - Multi-valued fields match any member.
  - `where` expressions support `= != ~ !~ > >= < <=`, `|` and missing values.
  - A misspelled field or controlled value raises an error naming the closest valid values.
- New methods: `fields()`, `values()`, `summary(by=...)`, `list_studies()`, `study()`, `card()`, `select()`,
  `head()`, `compact()`, `to_pandas()`, `export()` (json/csv/tsv/jsonl/parquet), `from_selection()` and
  `cite()` (BibTeX/text/JSON for the paper, the release and every GEO series).

**Gene search**

- New feature index: the measured `var` features of every sample, with identical panels stored once.
  - For the paper release it is published in the public catalog repository (commit `080eaea`, 41.9 MB),
    so gene search needs no login.
- `search(genes=..., gene_match=...)`, CLI `--gene`/`--gene-any` and MCP `genes` select samples by the
  genes or feature IDs they measure.
- `catalog.features(gsm)`, `sda-db features` and the MCP tool `list_features` list one sample's features
  without downloading it. A builder for maintainers is in `tools/build_feature_index.py`.

**Downloads**

- `plan_download()` gives bytes per sample and in total (from the release manifest), local reuse and free
  space, before any transfer.
- `download()` fetches samples in parallel, with a `max_bytes` limit, `on_error="continue"` and a saved
  report.

**Reading**

- `sda_db.load()` goes from a GSM to AnnData in one call, recording release, commit, units and the full
  sample.json in `adata.uns['sda_db']`. It can also drop the listed features.
- `sda_db.image` reads pyramid levels, regions (tile-wise), thumbnails and exact-size patches.
- `describe_sample()` gives a compact summary of a downloaded sample.

**Errors**

- Typed errors with stable codes and hints: `AccessError`, `NotFoundError`, `UnknownFieldError`,
  `UnknownValueError`, `IntegrityError`, `NetworkError`, `SizeLimitError` and `DiskFullError`. The ones that
  replace 0.2 `ValueError`s still subclass `ValueError`.

**CLI**

- New commands: `search`, `fields`, `values`, `summary`, `show`, `download` (`--dry-run`, `--max-size`, a
  20 GB guard, `--workers`, `--keep-going`, `--from FILE|-`), `verify` (whole folders), `inspect`, `cite`,
  `releases`, `doctor` and `mcp`.
- Tables on a terminal and compact JSON when piped, with errors as JSON on stderr. Exit codes 2–8 by error
  class.

**Agents**

- `sda-db mcp` is a dependency-free MCP stdio server with 13 tools.
  - It negotiates protocol versions and sends server instructions.
  - Downloads are plan-first and capped in size, and they write only to a server-controlled directory.
- Added an agent skill (`skills/sda-db/SKILL.md`), `AGENTS.md` and `llms.txt`.

**Breaking changes**

- The first positional argument of `Catalog.search` is now a ranked keyword query, in which every term must
  match a word. In 0.2 it was a literal substring.
  - If no keyword matches at all, the search falls back to the 0.2 substring match and adds a warning, so
    `search("hippo")` still works.
  - The old behaviour stays available as `text=`.
- Unknown values of controlled fields (platform, organism, split…) now raise `UnknownValueError`. It is a
  `ValueError` subclass with suggestions; 0.2 returned an empty selection.
- The default release moved from `8f116b8` to the paper tag commit `999566b`, so default downloads go to
  `downloads/999566b…/`.

**License**

- The toolkit code is now under the MIT License.

**Docs and tests**

- New and rewritten docs: CLI reference, agents and MCP, cookbook, Python API.
- 113 offline tests (68 new), plus a bounded live acceptance script (`tests/live_acceptance.py`).
- Fixed before release, after an independent code review:
  - negated `-prefix*` and `-"phrase"` terms;
  - queries made only of exclusions;
  - `--from` with CSV, JSONL and search JSON;
  - ids/csv output stopping at 20 rows;
  - stopping downloads on Ctrl-C;
  - a fail-closed size guard;
  - strict MCP argument types and UTF-8 stdio;
  - SuperSeries study aggregates;
  - the order of ties in descending sorts;
  - half-up rounding of patch origins.

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
