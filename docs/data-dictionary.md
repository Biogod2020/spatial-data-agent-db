# Data dictionary

A published sample has `sample.json`, `spatial.h5ad` and an optional `image.tif`.
The Hub catalog is at `catalog/samples.csv` and `catalog/samples.parquet`.
The client uses CSV; catalog access does not require Pandas or Arrow.

The [schema snapshot](../schemas/release-schema.json) contains the complete
`sda-release-sample-1` JSON Schema and release catalog dictionary.
[Provenance](../schemas/provenance.json) binds this snapshot to its SHA256.

## Catalog columns

Text is preserved as released; empty strings remain empty. The CSV reader decodes
`has_image` and `has_spatial_um` to booleans, `n_obs` and `n_vars` to integers, and
`um_per_unit` and `image_um_per_px` to floats or `None` for missing values.
Organism, tissue and technology labels are not mapped to ontologies by the client.

| Column | Published meaning |
|---|---|
| `gsm` | GEO sample (canonical GSM of its content group) |
| `gse` | all GEO series listing the GSM, joined with '+'; the split is computed on this string |
| `published_gse` | series folder of the sample: samples/<published_gse>/<gsm> |
| `source_pipeline` | sda-all (agent standardization, independent scorer) or hesrt (H&E Visium/ST, human review) |
| `source_release` | SDA-All scorer version of the batch, sda-all-gap-20261009, or hesrt-v1.3 |
| `platform` | harmonized platform |
| `technology` | technology as written by the authors |
| `organism` | as written in GEO |
| `tissue` | as written in GEO |
| `observation_unit` | spot / bin / cell / bead |
| `n_obs` | observations |
| `n_vars` | features |
| `expression_state` | counts (integer X) / non-integer / unknown (SDA-All: as declared and scored) |
| `verification` | VERIFIED / ATTESTED (SDA-All scorer) or human_visual_review (HESRT) |
| `expression_sha256` | SHA256 of the sample's spatial.h5ad |
| `coordinate_units` | units of obsm['spatial']: um / pixel / native_grid / unstated |
| `um_basis` | verified / declared_unverified / platform_constant / unresolved |
| `um_per_unit` | micrometres per coordinate unit when known |
| `has_spatial_um` | obsm['spatial_um'] present (micrometre copy of non-um coordinates) |
| `has_image` | image.tif present |
| `image_stain` | stain of image.tif ('(heuristic)' = pixel-only call) |
| `image_stain_basis` | basis of the stain label |
| `image_um_per_px` | micrometres per image.tif pixel when known |
| `image_registration` | VERIFIED / PARTIAL / UNRESOLVED / FAILED / ABSENT (modality acceptance), WITHHELD (HESRT review FAIL: no image delivered), HESRT_HUMAN_REVIEW, empty = no registrable image |
| `image_sha256` | SHA256 of image.tif |
| `segmentation` | cell / nucleus boundary acceptance (sources in sample.json) |
| `molecules` | molecule table acceptance (sources in sample.json) |
| `molecules_use` | positions_only: molecules without usable cell IDs |
| `flags` | units_unstated / attested / summary_features_in_matrix / hesrt_review_fail / hesrt_fail_image_released / image_partial_coverage / image_review_fail |
| `qc_flags` | deposit QC flags (annotation only) |
| `drop_features` | features to drop before use |
| `review_decision` | latest author review of this expression file: PASS / FAIL / UNSURE |
| `review_category` | review category |
| `review_notes` | review notes |
| `content_group` | independence unit |
| `group_kind` | cross_gse_redeposit / library_alias |
| `aliases` | GSMs with identical content not listed as rows |
| `split` | train / val / test by series |
| `sample_dir` | samples/<published_gse>/<gsm> |

Releases after the paper release add four review columns:

| Column | Meaning |
|---|---|
| `frozen_paper_evaluation_eligible` | `false` for reviewed daily additions, which are not part of the frozen paper evaluation cohort; empty for paper-release samples (decoded to `None`) |
| `review_kind` | kind of review that admitted a daily addition, e.g. `human` |
| `review_model`, `review_evidence_sha256` | model and evidence hash of a delegated review, when one was used |

## Curated metadata v2 (default release)

Release `v1.1.1-20261106` also joins `metadata/v2/samples_metadata_v2.csv` (tag `metadata-v2.2-20261106`,
schema `sda-sample-metadata-2`, SHA256-checked) by `gsm`. Its columns are defined in
[`schema_v2.json`](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB-Catalog/blob/main/metadata/v2/schema_v2.json)
and listed by `sda-db fields`. Lists are `;`-joined strings, as in the CSV. Where v2 corrects or harmonizes a
column (`platform`, `organism`, `sex`, `review_category`, `review_notes`), the v2 value takes the name and the
earlier value is kept as `<name>_v3`. The client adds `annotation_min_confidence`, the lowest per-field confidence.
Version 2.2 (release `v1.1.1-20261106`) adds `sex_source` (extracted field / sample text / manual / not stated);
`manual` in `*_source` columns marks a human-review correction. Release `v1.1.0-20261105` keeps the 2.1 table,
which has no `sex_source`. The paper release (`--release paper`) has none of these columns.

## Atlas metadata joined by the client

`Catalog.open()` joins two release files to every row: `catalog/atlas-study-metadata.jsonl` (per series) and
`catalog/atlas-sample-metadata.jsonl` (per sample). The values are as reported in GEO. They are not mapped
to ontologies, and missing values stay `None`.

| Field | Meaning |
|---|---|
| `study_title` | GEO series title of `published_gse` (the study's summary, design and PubMed IDs are in `catalog.studies` and `catalog.study(gse)`) |
| `disease`, `condition` | disease and condition/group labels as reported |
| `age`, `sex`, `donor_id` | as reported, with original units and spellings (`male`, `M`…) |
| `publication_pmid`, `publication_doi` | linked publication |
| `counts_ready` | source-bound counts-capability check: VERIFIED / UNRESOLVED / ABSENT, or NOT_RECORDED for HESRT |

`catalog.card(gsm)` also returns the sample's `counts_evidence` (the deposit text that supports the counts
call) and its scorer `audit`. Field meanings are available offline through `catalog.fields()` and
`sda-db fields`.

## Expression and features

`spatial.h5ad` has a CSR `X`, unique observation/feature indices,
`obs['source_observation_id']`, and `var` columns `source_feature_id`,
`feature_name` and `feature_type`. It has no `raw` or expression layers.
`expression_state` distinguishes counts, non-integer and unknown states;
`verification` describes recorded evidence. Inspect these fields before choosing
count-based analysis.

Use `quality.drop_features` from `sample.json` to explicitly remove annotated
summary or unwanted features in an analysis copy. Keep downloaded files intact.
The reading example shows how to inspect identifiers and construct a feature mask.

## Coordinates and images

`obsm['spatial']` is n-by-2 or n-by-3 in units recorded under
`sample.json['coordinates']`. `obsm['spatial_um']` is an optional recorded
micrometre copy of coordinates originally in other units. Pixels, array grids
and micrometres must be distinguished. `um_basis` can be `verified`,
`platform_constant`, `declared_unverified` or `unresolved`; returning a coordinate
does not upgrade its evidence.

A released `image.tif` is a tiled pyramidal OME-TIFF. Its
`image.spatial_to_pixel` matrix maps **native spatial x/y** to level-0 pixels:
`[x, y, 1] @ T.T`, followed by homogeneous division. Do not apply the transform
to `spatial_um` unless that is itself the native coordinate frame.
For a lower pyramid level, multiply x by `level_width / base_width` and y by
`level_height / base_height`. Do not assume a power-of-two downsampling factor
or interchange row/column with x/y. See [image_pyramid.py](../examples/image_pyramid.py).
Three-dimensional coordinates require an explicit projection before image use.

## Evidence, inclusion and auxiliary assets

Structural scoring (`verification`), image/modality acceptance
(`image_registration`) and human review (`review_decision`, `quality.review`)
are distinct. An empty review value does not mean PASS. QC flags are annotations;
the toolkit preserves them rather than choosing a universal exclusion rule.

`segmentation` and `molecules` record acceptance and source references. They do
not imply that additional boundary or molecule files are in the three-file sample
folder. Consult `sample.json` and the original source for those assets.
`provenance` may refer to publisher-side paths; those are historical references,
not paths users need or the client will attempt to download.

Use `sample_dir` / `published_gse` to locate files. `gse` can contain multiple
series joined with `+`; it is not always a single directory name.
Missing or withheld images are different from failed downloads.
