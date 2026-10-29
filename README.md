# SpatialDataAgent-DB · User toolkit

Find relevant spatial transcriptomics samples, download the files you need, and
read expression, coordinates and images with their release metadata.

[中文使用说明](README_zh.md) · [Data dictionary](docs/data-dictionary.md) ·
[Python API](docs/python-api.md) · [Streaming](docs/streaming.md) · [Examples](examples/) · [Validation](docs/validation.md) ·
[Database on Hugging Face](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB)

This repository is the user-facing companion to
[SpatialDataAgent](https://github.com/YSTLab/spatial-data-agent). It works independently
of the discovery and curation pipeline. The database stays on Hugging Face; this
repository contains a small Python client, CLI, documentation and examples.
Changes between SDK versions are recorded in [CHANGELOG.md](CHANGELOG.md).

## Start here

Python 3.11 or later:

```bash
git clone https://github.com/Biogod2020/spatial-data-agent-db.git
cd spatial-data-agent-db
python -m pip install -e .
```

The Hub repository may require access approval. Visit the database page, accept
its access conditions with your own account, and authenticate locally:

```bash
hf auth login
```

Credentials use the standard Hugging Face login/cache or `HF_TOKEN` environment
variable. Do not put tokens in scripts or command arguments. See
[access and troubleshooting](docs/access.md).

## Search first

Searching downloads only the catalog, with no expression matrices or images:

```bash
sda-db search --where 'platform=Visium' --where 'organism=Homo sapiens' \
  --where 'has_image=true' --where 'split=train' --output selection.json
sda-db search --text brain --min-obs 1000 --limit 5
```

`--where` filters are exact, case-insensitive matches combined with AND.
`--text` matches literal substrings across catalog fields. GSE filters match any
series listed for a sample. This is metadata filtering; it makes no model calls.
The full selection is saved even when `--limit` shows fewer rows.

## Download selected samples

Use canonical GSM IDs returned by search. For example, this sample is in the
default pinned catalog:

```bash
sda-db download GSM3036911 --destination downloads
```

Each sample contains `sample.json` and `spatial.h5ad`. Add `--include-image` to
also fetch `image.tif` when a released image exists. Multiple GSMs can follow
`download`; each asset is checked against its SHA256 and valid files are reused.
Images are opt-in because whole-slide files can be large.

Files are separated by their full database commit:

```text
downloads/<commit>/
├── samples/<GSE>/<GSM>/
│   ├── sample.json
│   ├── spatial.h5ad
│   └── image.tif             # when requested and available
└── .sda/receipts/<GSM>.json   # source commit, catalog hash and file hashes
```

Verify a downloaded sample without networking:

```bash
sda-db verify downloads/8f116b867ce4097957d4209db712d37bae3c9369/samples/GSE111672/GSM3036911
```

## Use Python

```python
from sda_db import Catalog, download_sample

catalog = Catalog.from_hub()
selected = catalog.search(platform="Visium", organism="Homo sapiens",
                          has_image=True, split="train")
selected.save_selection("selection.json")

# Inspect metadata before choosing how much to download.
for row in selected.rows[:2]:
    print(row["gsm"], row["tissue"], row["n_obs"], row["expression_state"])

# Explicitly select a GSM from the catalog.
directory = download_sample(catalog, "GSM3036911", "downloads")
```

Install optional analysis dependencies to read AnnData:

```bash
python -m pip install -e '.[analysis]'
```

```python
from sda_db.io import read_sample, coordinates

adata, metadata = read_sample(directory)  # backed='r'; matrices stay on disk
xy = coordinates(adata, metadata)         # original coordinate units
print(adata.shape, metadata["coordinates"], metadata["quality"])
adata.file.close()
```

Use [the reading example](examples/read_sample.py) to inspect features and apply
the explicit `drop_features` annotation in memory. The toolkit does not normalize
expression, filter observations or change units automatically.

## Stream samples with a disk budget

Version 0.2 adds bounded sample streaming: analyze one verified sample while the
next is prefetched, and let the SDK recycle released cache entries.

```python
from sda_db import stream_samples

with stream_samples(selected, cache_dir=".sda-cache", max_cache_bytes=10 * 1024**3,
                    prefetch=1) as samples:
    for sample in samples:
        adata, metadata = read_sample(sample.directory, verify=False)
        try:
            print(sample.gsm, adata.shape)
        finally:
            adata.file.close()
```

Close files before advancing: the previous sample's cache directory may be
evicted. File sizes are reserved before transfer, images remain opt-in, and each
sample must fit within the payload budget. The dedicated cache stores one
full-size copy of each asset, with additional small bookkeeping files.
For permanent downloads with the same direct transfer mode, use
`download_sample(..., storage="direct")` or `sda-db download ... --storage direct`.
See [streaming and cache lifetimes](docs/streaming.md) and the
[runnable example](examples/stream_samples.py).

## Choose a database version

The client defaults to commit
`8f116b867ce4097957d4209db712d37bae3c9369`, recorded in
[releases.json](releases.json). This keeps examples reproducible as the database
grows. To intentionally inspect a newer version:

```bash
sda-db search --revision main --where 'platform=Xenium' --limit 5
```

Or `Catalog.from_hub(revision="main")`. A branch or tag is resolved once to a
full commit; that commit is used for the catalog and subsequent sample downloads.
Use the printed commit in later commands to reproduce that selection. `--offline`
uses cached Hub files and requires a full commit hash.

## Interpret data correctly

- `X` is not uniformly raw counts: inspect `expression_state` and `verification`.
- `obsm['spatial']` retains the units in `sample.json`. Micrometres are available
  only when recorded; `um_basis` describes their evidence.
- Images are optional. Use `image.spatial_to_pixel` on native coordinates for
  level-0 pixels; scale x/y separately for lower pyramid levels.
- Keep release `split`, series membership, content groups and aliases when
  constructing train/test cohorts. Observation counts mix cells, spots, bins and beads.
- Structural verification, modality acceptance and human review have separate fields.
  Preserve missing values, QC flags and source annotations when selecting data.

See the [data dictionary](docs/data-dictionary.md) and [reuse guide](docs/reuse.md).

## Development

```bash
python -m pip install -e . numpy
python -m unittest discover -s tests -v
```

Tests use synthetic byte payloads and disable network connections. They check
client behavior and hashes; the publisher's scientific validators remain in
the main SpatialDataAgent repository. See [contributing](CONTRIBUTING.md).
