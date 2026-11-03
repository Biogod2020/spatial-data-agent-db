# Cookbook

Short, complete recipes. Each one runs as written after `pip install 'spatial-data-agent-db[analysis]'`.
Recipes that download need approved access.

```python
import sda_db
catalog = sda_db.open_catalog()          # paper release; reused from the local cache after the first call
```

## 1. A human H&E Visium cohort for image–expression learning

```python
cohort = catalog.search(platform="Visium", organism="human", image_stain="H&E",
                        expression_state="counts", where=["image_registration!=FAILED", "qc_flags="])
print(cohort.summary(by="split")["groups"])         # samples, studies and observations per split
train = cohort.search(split="train")
plan = train.plan_download(include_image=True)
print(plan.to_dict(per_sample=False)["total"])      # check the size before transferring
train.save_selection("he_visium_train.json")         # replayable record: commit, catalog hash, rows
```

`qc_flags=` keeps samples without deposit QC flags. Each QC rule you add is your own choice, so record it
with the selection; the release does not impose one.

## 2. Every sample of one study as AnnData

```python
import anndata as ad

study = catalog.study("GSE111672")
print(study["title"], study["samples"], study["pubmed_ids"])
adatas = {gsm: sda_db.load(gsm, catalog=catalog) for gsm in catalog.search(gse="GSE111672").gsms}
combined = ad.concat(adatas, label="gsm", join="inner", index_unique="-")   # shared features only
```

Concatenate only samples whose `expression_state` and feature identifiers agree. Studies differ in gene IDs
and panels.

## 3. Image patches around every spot

```python
import json
import numpy as np
from sda_db.image import read_patch
from sda_db.io import image_coordinates

adata = sda_db.load("GSM8915497", catalog=catalog, include_image=True)
meta = json.loads(adata.uns["sda_db"]["sample_json"])
pixels = image_coordinates(adata, meta)                      # level-0 pixels, one row per observation
directory = adata.uns["sda_db"]["directory"]
patches = [read_patch(directory, xy, 224)[0] for xy in pixels[:256]]
# Patches at the slide edge are clipped: keep only full-size ones.
keep = [i for i, p in enumerate(patches) if p.shape[:2] == (224, 224)]
X = np.stack([patches[i] for i in keep]); y = adata.X[keep]
```

`meta["image"]["um_per_px"]` gives the physical scale of level 0, and `sda_db.image.image_levels(directory)`
gives the scale of each pyramid level. Use a lower level (`read_patch(..., level=1)`) for a larger field of
view.

## 4. Stream a large cohort within a disk budget

```python
from sda_db.io import read_sample

xenium = catalog.search(platform="Xenium", organism="human")
with xenium.stream(cache_dir=".sda-cache", max_cache_bytes=50 * 1024**3, prefetch=2) as samples:
    for sample in samples:
        adata, meta = read_sample(sample.directory, verify=False)    # verified on arrival
        try:
            print(sample.gsm, adata.shape)
        finally:
            adata.file.close()                                       # close before the next sample
```

The next sample downloads while you work on the current one. Released samples are evicted when space is
needed. See [streaming](streaming.md).

## 5. Micrometre coordinates only when the release records them

```python
import json
from sda_db import coordinates

adata = sda_db.load("GSM9448988", catalog=catalog)
meta = json.loads(adata.uns["sda_db"]["sample_json"])
try:
    xy_um = coordinates(adata, meta, units="um")
except ValueError:
    xy_um = None            # unresolved scale: keep native units, do not guess a pixel size
print(meta["coordinates"]["um_basis"])   # verified / platform_constant / declared_unverified / unresolved
```

## 6. What is in the atlas

```python
catalog.summary(by=["platform", "organism"])["groups"][:10]
catalog.values("image_stain")
catalog.search("lung fibrosis").list_studies()[:5]
catalog.to_pandas().groupby("platform")["n_obs"].describe()
```

## 7. Reproduce a selection months later

```python
cohort = sda_db.Catalog.from_selection("he_visium_train.json")   # same commit, same samples, checked hash
cohort.download("downloads", include_image=True, max_bytes="500GB")
```

## 8. New samples since the paper release

```python
latest = sda_db.open_catalog("latest")                 # needs approved access; resolves main to a commit
new = latest.search(where="frozen_paper_evaluation_eligible=false")
print(latest.release_name, latest.revision[:7], len(new))
print(new.summary(by="platform")["groups"])
```

Keep paper evaluations on the frozen release. Daily additions are reviewed, but they are not part of the
paper's evaluation cohort.

## 9. Ask an agent

With the MCP server connected ([agents](agents.md)), requests like these work end to end:

- "Find human Visium samples of Alzheimer's disease with H&E images and summarize them by study."
- "How many Xenium samples have more than 5,000 genes? Plan the download of the five smallest."
- "Download GSM3036911, tell me its coordinate units and whether X holds raw counts, and give me the BibTeX."
