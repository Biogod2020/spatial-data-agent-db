# SpatialDataAgent-DB toolkit

Find, download, verify, read and cite samples from
[SpatialDataAgent-DB](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB), a versioned atlas of
public spatial transcriptomics. The paper release holds **12,163 samples from 1,265 GEO studies** in 12
platform groups (Visium, Visium HD, Xenium, CosMx, MERFISH/MERSCOPE, Slide-seq, Stereo-seq, legacy ST and others).
**8,211 samples have a registered tissue image.** Every sample has verified expression, coordinates and
release metadata.

One package serves three kinds of users:

- **People** use a Python API and a CLI that prints readable tables.
- **Scripts** get JSON output, stable error codes and replayable selections.
- **AI agents** get an MCP server, compact JSON and an agent skill. They can search, plan downloads and
  read samples safely.

**Searching needs no account.** The catalog of the paper release is mirrored in an ungated repository and
bound to the release by SHA256. Downloading sample files needs approved access ([below](#access)).

[中文说明](README_zh.md) · [CLI](docs/cli.md) · [Python API](docs/python-api.md) ·
[AI agents and MCP](docs/agents.md) · [Cookbook](docs/cookbook.md) · [Data dictionary](docs/data-dictionary.md) ·
[Streaming](docs/streaming.md) · [Validation](docs/validation.md) · [Changelog](CHANGELOG.md)

## Install

Python 3.11 or later:

```bash
pip install "spatial-data-agent-db[analysis] @ git+https://github.com/Biogod2020/spatial-data-agent-db"
```

The `analysis` extra adds AnnData, NumPy, pandas and tifffile for reading samples. Searching and downloading
need only the core install. To run the CLI without installing anything:

```bash
uvx --from "git+https://github.com/Biogod2020/spatial-data-agent-db" sda-db search alzheimer
```

## Command line in one minute

```bash
sda-db search alzheimer hippocampus --organism human        # ranked; no download, no login
sda-db values tissue --platform Xenium --top 15            # learn the vocabulary of a field
sda-db search --gene GFAP --gene AQP4 --platform Xenium    # samples whose panel measures both genes
sda-db features GSM9448988 --contains CD                   # genes a sample measures, without downloading
sda-db show GSM8185219                                     # everything about one sample
sda-db download GSM8185219 --dry-run                       # sizes before any transfer
sda-db download GSM8185219                                 # verified against SHA256, with a receipt
sda-db inspect GSM8185219                                  # shape, coordinates, QC, image levels
sda-db search --platform "visium hd" --has-image --output cohort.json
sda-db download --from cohort.json --include-image --max-size 100GB
sda-db cite --from cohort.json -o refs.bib                 # database + every original study
sda-db doctor                                              # login, access, disk, network
```

The CLI prints a table on a terminal and compact JSON when its output is piped. `--json` forces JSON.
Commands compose through IDs:

```bash
sda-db search lung --platform Xenium --format ids | sda-db download --from - --dry-run
```

See the [CLI reference](docs/cli.md) for every option, JSON envelope and exit code.

## Python in one minute

```python
import sda_db

catalog = sda_db.open_catalog()                  # paper release v1.0.0-20261025
hits = catalog.search("alzheimer hippocampus", organism="human", has_image=True)
print(hits)                                      # <Catalog release=v1.0.0-20261025 commit=999566b samples=... >
hits.to_pandas()[["gsm", "tissue", "disease", "n_obs", "score"]]
hits.list_studies()                              # one row per GEO series, with titles and PubMed IDs

print(hits.plan_download(include_image=True).to_dict(per_sample=False))   # bytes before transfer
report = hits.download("downloads", include_image=True, max_bytes="50GB")

adata = sda_db.load("GSM8185219")                # download (verified) + read as AnnData
adata.uns["sda_db"]                              # release, commit, units, stain, full sample.json
```

Cut image patches around spots or cells for image–expression models:

```python
import json
from sda_db.image import read_patch
from sda_db.io import image_coordinates

adata = sda_db.load("GSM8915497", include_image=True)
meta = json.loads(adata.uns["sda_db"]["sample_json"])
pixels = image_coordinates(adata, meta)          # level-0 image pixels of every observation
patch, info = read_patch(adata.uns["sda_db"]["directory"], pixels[0], 224)
```

For many samples under a disk budget, `catalog.stream(...)` delivers verified samples one at a time and
prefetches the next ([streaming](docs/streaming.md)). See the [Python API](docs/python-api.md) and the
[cookbook](docs/cookbook.md) for cohorts, splits, images and more.

## AI agents

Register the MCP server once and an agent can search, inspect, plan, download and cite:

```bash
claude mcp add sda-db -- sda-db mcp                       # Claude Code
```

The server needs no extra packages. It offers 13 tools: `search_samples` (keywords, filters and genes),
`field_values`, `list_fields`, `summarize`, `get_sample`, `get_study`, `list_features`, `plan_download`,
`download_samples`, `inspect_sample`, `cite`, `list_releases` and `check_access`. Downloads are plan-first. They need `confirm=true`, stop above a size
limit you set, and write only to a directory you choose. Configuration for Claude Desktop, Codex, Cursor,
Gemini CLI and VS Code is in [AI agents and MCP](docs/agents.md). An agent without MCP can use the JSON CLI
with the [agent skill](skills/sda-db/SKILL.md).

## Search

| Syntax | Meaning |
|---|---|
| `alzheimer hippocampus` | Ranked keywords (BM25) over tissue, disease, organism, platform, IDs and study title/design/summary. Every term must match. |
| `"spinal cord"` · `-mouse` · `neuro*` | Phrase · exclude · prefix |
| `--platform "visium hd"` · `--organism human` | Exact values. Case, spaces and hyphens are ignored, and common names work. Repeat for OR. |
| `--tissue cortex` · `--disease glioma` | Case-insensitive substring of the GEO text |
| `--gene GFAP --gene AQP4` (`--gene-any`) | Samples that measure these features: symbols or Ensembl IDs, any case. Measured is not the same as detected. |
| `--where "n_obs>=50000"` · `"split=train\|val"` · `"disease="` | Any field: `=`, `!=`, `~` (contains), `!~`, `>`, `>=`, `<`, `<=`, `\|` for OR, empty for missing |

Search is deterministic. It runs offline after the first catalog download and makes no model calls.

- A misspelled field or controlled value fails with suggestions, e.g. `platform='Visum'` →
  `'Visium', 'Visium HD'`.
- Terms that match nothing are reported, not silently dropped.
- The study-level [intent search](https://github.com/YSTLab/spatial-data-agent) of the paper is a separate,
  model-based tool.

## Releases and reproducibility

| Release | Commit | Samples | Notes |
|---|---|---:|---|
| `v1.0.0-20261025` (default; aliases `paper`, `v1`) | `999566b` | 12,163 | Paper release; the catalog is ungated and SHA256-bound |
| `latest` | resolved at open | grows | Rolling release with reviewed daily additions; needs access |

Every selection, download and citation records its release name and full commit. Use `--release` or
`open_catalog(release=...)` to choose one. `Catalog.from_selection("cohort.json")` reopens the same commit and
the same samples. Downloads go to `downloads/<commit>/samples/<GSE>/<GSM>/` with a receipt of every file hash.

## Access

Sample files are on Hugging Face and require **manual approval with an institutional e-mail**:

1. Request access at the [dataset page](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB).
2. When approved, run `hf auth login`, or set `HF_TOKEN`.
3. Run `sda-db doctor` to confirm.

Keep tokens out of scripts and command arguments. See [access and troubleshooting](docs/access.md).

## Interpret the data correctly

- `X` is not raw counts everywhere. Check `expression_state` (counts / non-integer / unknown) and `verification`.
- `obsm['spatial']` keeps its original units (`coordinate_units`: um / pixel / native_grid / unstated).
  Micrometres exist only where recorded, and `um_basis` says how they were obtained.
- Observations are spots, bins, cells or beads (`observation_unit`), not cells everywhere.
- Images are optional and opt-in. `image.spatial_to_pixel` maps native x/y to level-0 pixels.
  Heuristic stain labels say so.
- For machine learning keep the release `split`, keep each study together, and check `content_group` and
  `aliases`.
- Verification, image registration and human review are separate fields, and an empty review is not a PASS.

See the [data dictionary](docs/data-dictionary.md) and [reuse guide](docs/reuse.md).

## Cite

`sda-db cite` writes BibTeX for the database paper, the release and every GEO series of a selection. Cite the
original studies as well. The paper:

> Ji J-H, Zou Q, Cheng J, et al. *An agent-built, verified atlas of public spatial transcriptomics shows
> that breadth across studies drives transfer and reproducibility.* bioRxiv (2026).
> [doi:10.64898/2026.05.27.727615](https://doi.org/10.64898/2026.05.27.727615)

## License

The toolkit code is under the [MIT License](LICENSE). The data keep the terms of the dataset card and of the
original studies; this license does not cover them.

## Development

```bash
python -m pip install -e '.[analysis]'
python -m unittest discover -s tests -v
```

Tests are offline with synthetic payloads. They cover search, filters, releases, plans, parallel downloads,
integrity checks, the CLI contract, the MCP protocol and image reads. Live checks against the Hub are
recorded in [validation](docs/validation.md). This repository is the user-facing companion of
[SpatialDataAgent](https://github.com/YSTLab/spatial-data-agent), which builds the atlas.
See [contributing](CONTRIBUTING.md).
