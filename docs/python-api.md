# Python API

Core access uses `huggingface_hub` and `filelock`.
AnnData, NumPy and TIFF support are optional via the `analysis` extra.

```python
from sda_db import Catalog, download_sample, verify_sample, stream_samples
```

| Call | Result |
|---|---|
| `Catalog.from_hub(revision=..., local_files_only=False, cache_dir=None)` | Read `catalog/samples.csv`; resolve mutable references once |
| `Catalog.from_csv(path, revision=<full commit>, ...)` | Read a local UTF-8 CSV with explicitly supplied source commit |
| `catalog.rows` | List of catalog dictionaries; booleans/numbers decoded, text preserved |
| `catalog.get(gsm)` | Canonical GSM row; absent/alias-only IDs raise `ValueError` |
| `catalog.search(text=None, min_obs=None, max_obs=None, **filters)` | New catalog retaining the original commit and catalog hash |
| `selected.save_selection(path)` | JSON export of all selected rows, commit and catalog hash |
| `download_sample(catalog, gsm, destination='downloads', include_image=False, storage='hub')` | Verified permanent directory; `'direct'` avoids an extra full-size copy |
| `verify_sample(directory, require_image=False)` | Offline hash report; raises on missing/corrupt requested assets |
| `stream_samples(catalog, gsms=None, cache_dir='.sda-cache', max_cache_bytes=10*1024**3, prefetch=1, include_image=False)` | Context-managed sample iteration with prefetch and bounded cache |

Exact filters: `gsm`, `gse`, `published_gse`, `platform`, `organism`, `tissue`,
`split`, `source_pipeline`, `source_release`, `observation_unit`, `expression_state`,
`verification`, `coordinate_units`, `um_basis`, `image_stain`, `image_registration`,
`review_decision`, `has_image`, `has_spatial_um`. Boolean filters take Python booleans.
Unsupported filter names raise rather than silently returning an unfiltered catalog.

`from_csv` records the local file's hash, but cannot establish that a caller-supplied
CSV belongs to the supplied commit. Use `from_hub` for source-bound access.
Selection exports are records for inspection, not executable download instructions.
To replay one, open its recorded commit and select its GSM IDs again:

```python
import json
record = json.load(open("selection.json", encoding="utf-8"))
catalog = Catalog.from_hub(revision=record["revision"], repo_id=record["repo_id"])
if catalog.catalog_sha256 != record["catalog_sha256"]:
    raise ValueError("Catalog bytes differ from selection record")
for row in record["samples"]:
    download_sample(catalog, row["gsm"], "downloads")
```

Reading and coordinates:

```python
from sda_db.io import read_sample, coordinates, image_coordinates
adata, meta = read_sample(directory, backed="r", verify=True)
xy_native = coordinates(adata, meta)
# Available only when recorded, with evidence described by coordinates.um_basis:
# xy_um = coordinates(adata, meta, units="um")
# For samples with images and 2D coordinates:
# xy_pixels_level0 = image_coordinates(adata, meta)
adata.file.close()
```

`read_sample` supports `backed='r'` and `backed=None`. It verifies local hashes by
default, which reads the complete asset bytes with bounded memory. After an
unchanged file has been verified, `verify=False` avoids this additional pass.
`backed=None` loads the expression object into memory; select it deliberately.
No expression preprocessing, feature removal, coordinate inference or QC selection
is performed by these functions. Close the AnnData file after backed reads.

Downloads use the Hub's standard credentials, cache, resumable transport and
network configuration. A complete download receipt is written after all requested
files pass hash checks. Hash errors preserve existing files and have no automatic
overwrite option. A partial attempt may leave verified metadata/assets; retrying
checks and reuses them. Batch CLI downloads run sequentially, stopping on errors.
`storage='direct'` uses destination-local staging and installs verified files
without copying their full contents into a second destination. Completed local
metadata are reused only when their receipt matches the repository, revision,
GSM, sample directory and metadata hash.

See [streaming](streaming.md) for sample leases, cache eviction, size admission,
offline reuse and shutdown behavior. Streaming uses complete verified sample
files and does not implement file-internal remote slicing.

Local verification with a receipt additionally checks `sample.json` and receipt
identity; without a receipt, `receipt_verified` is false and the check establishes
local consistency with metadata. It does not establish source provenance or
re-run structural, biological or human-review checks.
