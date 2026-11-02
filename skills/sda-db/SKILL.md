---
name: sda-db
description: Find, download and read public spatial transcriptomics samples (Visium, Visium HD, Xenium, CosMx, MERFISH, Slide-seq, Stereo-seq, legacy ST) from the SpatialDataAgent-DB atlas, with verified AnnData expression, coordinates and registered tissue images. Use when a task needs spatial transcriptomics data by tissue, disease, organism, platform, stain or GEO accession; when building a training or evaluation cohort (e.g. H&E–expression pairs); or when citing the atlas or its source studies.
---

# SpatialDataAgent-DB

The paper release `v1.0.0-20261025` holds 12,163 samples from 1,265 GEO studies, and 8,211 of them have a
tissue image. Use the `sda-db` CLI, which prints JSON when piped, or the MCP tools with the same names if they
are connected. Python: `import sda_db`.

## Workflow

1. **Search** (no login needed):
   `sda-db search KEYWORDS [--organism human] [--platform "visium hd"] [--tissue T] [--disease D] [--has-image] [--limit N]`
   - Keywords are ranked and must all match sample fields or the study title/summary. Use
     `"quoted phrases"`, `-exclude` and `prefix*`.
   - `total` is the full match count. Only `shown` rows are returned.
   - Add `--studies` for one row per study, or `--output cohort.json` to save the full selection.
2. **Learn the vocabulary instead of guessing:** `sda-db values FIELD [filters]` and `sda-db fields`.
   - Tissue and disease are free GEO text, so prefer `--tissue` and `--disease`, which match substrings.
   - A wrong controlled value (platform, organism, stain, split…) returns exit code 2 with the closest
     values in `hint`. Retry with one of them.
3. **Inspect:** `sda-db show GSM…` (sample card) or `sda-db show GSE…` (study with all its samples).
4. **Plan, then download:**
   - `sda-db download --from cohort.json --dry-run` reports bytes, local files and free disk.
   - Then download with `--max-size 50GB`. Add `--include-image` only when images are needed; they are large.
   - The full release is about 1.8 TB. Never download without a plan.
5. **Read:**
   - `sda-db inspect GSM…` describes a downloaded sample.
   - In Python, `sda_db.load("GSM…")` returns AnnData, with `adata.uns["sda_db"]` holding the release,
     commit, units and the full sample.json.
   - `sda_db.image.read_patch(directory, xy_level0, 224)` cuts image patches;
     `sda_db.io.image_coordinates(adata, meta)` maps observations to image pixels.
6. **Cite:** `sda-db cite --from cohort.json -o refs.bib`. Always report the release name and commit with any
   selection.

## Interpret correctly (common mistakes)

- **Counts.** Not every matrix is raw counts. Use count-based methods only when `expression_state=counts`.
- **Units.** Coordinates keep their native units (`coordinate_units`: um / pixel / native_grid / unstated).
  Use micrometres only where recorded (`um_per_unit`, `obsm['spatial_um']`). `um_basis` says how reliable
  they are. Never assume pixel sizes.
- **Observations.** An observation is a spot, bin, cell or bead (`observation_unit`). Do not call them all
  cells.
- **Images.** `image_stain` labels ending in "(heuristic)" are pixel-based guesses. Missing images are
  missing; images are not failed downloads.
- **Machine learning.** Keep the release `split` (made by study) and keep each study's samples together.
  Never split observations of one section at random.
- **Evidence.** Verification, image registration and human review are separate fields. An empty
  `review_decision` is not a PASS.
- **Organisms.** Organism values are written as in GEO. A few samples list two species (for example a
  xenograft); `--organism mouse` matches any member.

## Access and errors

- **Access.** Downloading needs approved access with an institutional e-mail at
  https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB, then `hf auth login`.
  - `sda-db doctor` (MCP: `check_access`) diagnoses login, access and disk, with a fix for each failure.
  - Do not ask the user for tokens or put tokens in commands.
- **Exit codes:**

  | Code | Meaning |
  |---:|---|
  | 2 | typo, with suggestions |
  | 3 | not found or nothing selected |
  | 4 | access |
  | 5 | integrity |
  | 6 | network |
  | 7 | size limit |
  | 8 | disk full |

  The JSON error on stderr has `code`, `message` and `hint`.
- **Releases.** The default is the frozen paper release. `--release latest` adds reviewed daily samples and
  needs access.

Full docs: https://github.com/Biogod2020/spatial-data-agent-db (docs/cli.md, docs/agents.md, docs/cookbook.md).
