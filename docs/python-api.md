# Python API

```python
import sda_db
```

The core install needs only `huggingface_hub` and `filelock`. Reading AnnData and images needs the `analysis`
extra (anndata, NumPy, pandas, tifffile).

## Open a release

| Call | Result |
|---|---|
| `sda_db.open_catalog(release=None)` / `Catalog.open(release=None, source="auto", offline=False, cache_dir=None)` | The catalog of a release. It joins study titles, summaries and designs, and reported biology (disease, condition, age, sex, donor, publication). |
| `Catalog.from_selection(path)` | Reopens the commit of a saved selection and selects the same GSMs, after checking the catalog hash |
| `Catalog.from_csv(path, revision=<commit>)` | A local CSV with an explicitly supplied commit (no metadata join) |
| `Catalog.from_hub(revision=...)` | Client 0.1–0.2 behaviour: `catalog/samples.csv` from the gated dataset only |
| `sda_db.known_releases()`, `sda_db.resolve_release(name)` | Release registry; `latest` resolves `main` to a commit once |

- **Release names.** `release` takes a name (`v1.0.0-20261025`, the default), an alias (`paper`, `v1`), a tag,
  `latest`, or a full commit.
- **Catalog source.** `source="auto"` reads the catalog from the public mirror when the release has one, so
  no account is needed. Each mirrored file must match the SHA256 recorded for the release.
- **Downloads.** Sample files always come from the gated dataset, at the release commit.
- **Offline.** `offline=True` uses only cached files.

## Select

All selection methods return a new `Catalog` bound to the same commit, with the same study metadata.

| Call | Result |
|---|---|
| `catalog.search(query=None, *, where=None, min_obs=None, max_obs=None, match="all", sort=None, text=None, **filters)` | Ranked keywords plus filters (AND) |
| `catalog.select(gsms)` | Rows for the given GSMs, in that order; an unknown ID fails before any work |
| `catalog.get(gsm)` / `catalog.card(gsm)` | One row / a grouped card (identity, study, biology, assay, expression, coordinates, image, quality, files) |
| `catalog.head(n)`, `len(catalog)`, `iter(catalog)`, `gsm in catalog`, `catalog.gsms` | Basics |

**Keywords.** `query` is matched against sample fields (tissue, disease, condition, organism and its common
name, platform, technology, stain, GSM/GSE) and against the study title, design and summary.

- **All terms.** Every term must match, unless `match="any"`.
- **Syntax.** `"quoted phrases"`, `-excluded` terms and `prefix*` work.
- **Unmatched terms.** A term that matches nothing in the release is ignored and reported in `.warnings`.
- **Ranking.** Results are ordered by BM25. `catalog.scores` maps each GSM to its score, and
  `catalog.report["terms"]` gives the number of samples matching each term.

**Filters** (`platform="Visium HD"`, `organism="human"`, `split=["train", "val"]`, `has_image=True`) are
exact values.

- **Normalization.** Case, spaces, hyphens and brackets are ignored. Common names work for organism and
  platform.
- **OR and membership.** A list means OR. Multi-valued fields (`gse`, `organism`, `flags`, `qc_flags`,
  `aliases`) match any member.
- **Typos.** An unknown field raises `UnknownFieldError`, and an unknown controlled value raises
  `UnknownValueError`. Both carry `.hint` with the closest values.
- **Free text.** For free-text fields (tissue, disease…) an exact miss only adds a warning. Use
  `where="tissue~cortex"` for substrings.

**`where`** takes one expression or a list of them: `"n_obs>=50000"`, `"tissue~cortex"`, `"platform!=Visium"`,
`"disease="` (missing) and `"split=train|val"`. The operators are `= != ~ !~ > >= < <=`.

## Describe and export

| Call | Result |
|---|---|
| `catalog.fields()` | Every field with meaning, kind, distinct count, missing count and top values |
| `catalog.values(field, top=None)` | Distinct values with sample, study and observation counts |
| `catalog.summary(by=None)` | Totals, optionally cross-tabulated by one or more fields |
| `catalog.list_studies()` / `catalog.study(gse)` | One record per series / one series with its samples |
| `catalog.to_pandas(fields=None)`, `catalog.to_records(fields=None)` | DataFrame indexed by GSM / list of dicts (with `score` after a ranked search) |
| `catalog.compact(limit)` | Token-lean rows plus a `{GSE: title}` map, as the CLI and MCP return them |
| `catalog.export(path)` | `.json` (replayable selection), `.csv`, `.tsv`, `.jsonl` or `.parquet` |
| `catalog.cite(format="bibtex")` | BibTeX, `"text"` or `"json"`: paper, release and every GEO series |

## Plan and download

| Call | Result |
|---|---|
| `catalog.plan_download(destination="downloads", include_image=False)` | `DownloadPlan`: bytes per sample and in total, local files, free space, notes |
| `catalog.download(destination="downloads", include_image=False, workers=4, max_bytes=None, storage="hub", on_error="raise")` | `DownloadReport` with `.completed`, `.failed`, `.directories`, `.save(path)` |
| `download_sample(catalog, gsm, destination, include_image=False, storage="hub")` | One verified sample directory |
| `verify_sample(directory, require_image=False)` | Offline hash and receipt check |
| `catalog.stream(cache_dir=".sda-cache", max_cache_bytes=..., prefetch=1, include_image=False)` | Context-managed iteration under a disk budget ([streaming](streaming.md)) |

**Plans.** Sizes come from the release file manifest, with Hub path metadata for files the manifest does not
list. Both need approved access; without it the sizes stay `None` and a note explains why.

**Downloads.**

- **Size limit.** `max_bytes` (`"50GB"`) refuses the whole download, before any transfer, when the bytes still
  to fetch exceed it. It also refuses when sizes are unknown.
- **Verification.** Each file is checked against the release SHA256 before it is installed. Existing verified
  files are reused, and differing local files are never overwritten.
- **Failures.** `on_error="continue"` records failures and keeps going. `"raise"` re-raises the first error,
  with `.report` attached.

## Read

| Call | Result |
|---|---|
| `sda_db.load(gsm_or_directory, catalog=None, release=None, destination="downloads", include_image=False, backed=None, drop_features=False)` | AnnData; downloads a GSM first |
| `read_sample(directory, backed="r", verify=True)` | `(adata, sample_json)` |
| `coordinates(adata, meta, units="native" \| "um")` | Native or recorded micrometre coordinates; never inferred |
| `image_coordinates(adata, meta)` | Level-0 image pixels of every observation (2-D) |
| `sda_db.io.feature_mask(adata, meta)` | Boolean mask after the release's `drop_features` |
| `sda_db.io.describe_sample(directory)` | JSON-ready description (shape, columns, coordinate range, QC, image levels) |
| `sda_db.image.image_levels(directory)` | Width, height, axes, scale and µm/px of each pyramid level |
| `sda_db.image.read_image(directory, level="auto", max_size=2048, region=None)` | One level, or a region in level-0 pixels; tile-wise via zarr |
| `sda_db.image.read_patch(directory, center_xy, size, level=0)` | An exact `size × size` patch (clipped at slide borders) |
| `sda_db.image.thumbnail(directory, max_size=1024)`, `to_array_xy(xy_level0, info)` | Whole-slide preview / map level-0 pixels into a read array |

`load` adds `adata.uns["sda_db"]`, which holds:

- the GSM, release, commit and directory;
- the platform, organism and tissue;
- the expression state, units, `um_per_unit` and image stain;
- the full `sample.json`, as a JSON string (`sample_json`).

`load` changes nothing else, except that `drop_features=True` removes the listed features. The reader never
normalizes, filters observations or converts units. `backed="r"` keeps the matrix on disk; close
`adata.file` when done.

## Errors

Every SDK error subclasses `sda_db.SdaDbError` and has `.code`, `.hint` and `.to_dict()`:

| Error | Code | Raised when |
|---|---|---|
| `AccessError` | `access_denied` | dataset access is missing or not approved, or the token is invalid |
| `NotFoundError` | `not_found` | an identifier, release or cached file does not exist |
| `UnknownFieldError` | `unknown_field` | a filter names a field that does not exist |
| `UnknownValueError` | `unknown_value` | a controlled value matches nothing in the release |
| `IntegrityError` | `integrity` | bytes or metadata disagree with the release |
| `NetworkError` | `network` | the Hub cannot be reached |
| `SizeLimitError` | `size_limit` | a plan exceeds the size limit |
| `DiskFullError` | `disk_full` | the destination or cache is full or over quota |

`NotFoundError`, `UnknownFieldError`, `UnknownValueError`, `IntegrityError` and `SizeLimitError` are also
`ValueError`s, so code written for 0.2 keeps working.
