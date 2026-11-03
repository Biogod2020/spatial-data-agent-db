# Command line: `sda-db`

`sda-db --help` and `sda-db COMMAND --help` list every option. This page explains the shared conventions,
then each command.

## Conventions

**Output.** A terminal gets a table. A pipe or file gets compact JSON (`--json` forces JSON; `--format`
picks `table`, `json`, `jsonl`, `csv`, `tsv` or `ids` where offered). Notes and progress go to stderr, so
stdout stays machine-readable.

**Releases.** `--release NAME` chooses a release: a name, alias, tag, `latest` or a full commit. The default
is the paper release `v1.0.0-20261025`. `sda-db releases` lists the choices. Every JSON result names
`release` and `commit`.

**Selection.** `search`, `values`, `summary`, `download` and `cite` share the same filters, and all of them
must hold (AND):

| Option | Meaning |
|---|---|
| keywords (`search QUERY...`, or `-q/--query` elsewhere) | Ranked; every term must match sample fields or the study title/design/summary. `"phrase"`, `-exclude`, `prefix*`. |
| `--platform P` | Exact platform; aliases such as `"visium hd"`, `merfish`, `smi`. Repeat for OR. |
| `--organism O` | Scientific or common name (`human`, `mouse`, `rat`, `zebrafish`...). Repeat for OR. |
| `--tissue T`, `--disease D` | Case-insensitive substring of the GEO text. Repeat for OR. |
| `--gse GSE…` | Series membership, including secondary series. |
| `--split train\|val\|test`, `--stain "H&E"`, `--has-image`/`--no-image` | Exact |
| `--min-obs N`, `--max-obs N` | Observation count bounds (inclusive) |
| `--where EXPR` | Any field: `FIELD=V`, `!=`, `~` (contains), `!~`, `>`, `>=`, `<`, `<=`; `\|` = OR; `FIELD=` = missing |
| `--gene G` (repeat = AND; `--gene-any` = OR) | Samples whose measured features include G: a symbol or Ensembl ID, any case, with version suffixes ignored |
| `--any` | Keywords match if any term matches |
| `--sort FIELD` | Sort, `-FIELD` for descending (default: rank, then catalog order) |

`download` and `cite` also take GSM/GSE accessions as arguments, and `--from FILE` (`-` reads stdin). FILE
can be any of:

- a selection JSON from `--output`, which reopens its saved commit;
- `search --json` output, which uses its `commit`; it holds only the shown rows;
- a `.jsonl`, `.csv` or `.tsv` export, of which only its `gsm` column is read;
- free text with accessions. GSMs are used when present; GSE accessions are expanded only when the text
  names no GSM.

**Errors.** On failure the CLI prints `error:` and `hint:` lines. In JSON mode it prints instead
`{"error": {"code", "message", "hint"}}` on stderr, and exits with:

| Exit | Code | Typical cause |
|---:|---|---|
| 0 | — | success |
| 1 | `error` | unexpected failure (rerun with `-v`) |
| 2 | `unknown_field`, `unknown_value` | typo in a field or controlled value; the hint lists close matches |
| 3 | `not_found` | unknown GSM/GSE/release, or nothing selected |
| 4 | `access_denied` | no approved dataset access, or an invalid token |
| 5 | `integrity` | a file or receipt differs from the release |
| 6 | `network` | Hub unreachable, rate-limited, or offline cache miss |
| 7 | `size_limit` | the download plan exceeds `--max-size` or the 20 GB safety limit, or sizes are unknown |
| 8 | `disk_full` | destination or cache full, or over quota |

## search

```bash
sda-db search glioblastoma --has-image
sda-db search "spinal cord" ALS --organism human --limit 50
sda-db search --platform Xenium --where "n_vars>=5000" --sort -n_obs --format csv > xenium.csv
sda-db search kidney --organism mouse --studies         # one row per study
sda-db search breast cancer --output cohort.json        # full selection, replayable
```

The JSON result has this shape:

```json
{"release": "v1.0.0-20261025", "commit": "999566b…", "query": "glioblastoma", "filters": ["has_image=True"],
 "total": 262, "shown": 20, "truncated": true,
 "samples": [{"gsm": "GSM9572843", "published_gse": "GSE324268", "platform": "Xenium", "organism": "Homo sapiens",
              "tissue": "Brain", "disease": "Glioblastoma", "n_obs": 851509, "observation_unit": "cell",
              "has_image": true, "image_stain": "fluorescence: DAPI", "expression_state": "counts",
              "coordinate_units": "um", "split": "train", "score": 11.75}],
 "studies": {"GSE324268": "Xenium single cell spatial transcriptomics from patient-derived glioblastoma …"},
 "warnings": [], "term_matches": {"glioblastoma": 278}, "next": ["sda-db show GSM9572843", "…"]}
```

- Fields that are empty for a sample are left out, and each study title appears once in `studies`.
- `--fields all` (or a comma list) returns full rows instead.
- `--limit` sets how many rows are shown: 20 by default for `table`/`json`, and every match for
  `ids`/`csv`/`tsv`/`jsonl` (a note on stderr says when rows were cut). `--all` shows every match.
- `--output` always saves the complete selection: `.json` is replayable; `.csv`, `.tsv`, `.jsonl` and
  `.parquet` save tables.

## fields, values, summary

```bash
sda-db fields                                  # every field: meaning, kind, distinct values, top values
sda-db values platform
sda-db values tissue --organism human --platform Visium --top 30
sda-db values image_stain --has-image
sda-db summary --by platform,split --has-image
```

Use `values` before filtering on a free-text field. The vocabulary is as written in GEO and not harmonized,
so `brain` and `Brain` are separate values, and `--tissue brain` matches both.

## Genes: `--gene` and `features`

```bash
sda-db search --gene GFAP --gene AQP4 --organism human --where "n_vars<=10000"   # targeted panels with both
sda-db search --gene ERBB2 --gene-any --gene ESR1 --platform Xenium --studies
sda-db features GSM9448988                       # every feature of one sample, with its type
sda-db features GSM9448988 --contains CD --format csv
```

Gene search reads the release's **feature index**, a SQLite file built from every sample's `var` table. It
is downloaded once (42 MB, from the public catalog repository, no login needed), checked against its SHA256,
and then queried locally.

- **Rolling release.** `--release latest` reuses the paper-release index. Samples added later are reported as
  not indexed.

- **Measured, not detected.** A gene in the index was measured; it was not necessarily detected.
- **Whole-transcriptome assays.** Visium and similar assays measure almost every gene, so add
  `--where "n_vars<=10000"` to find targeted panels (Xenium, CosMx, MERFISH…).
- **Matching.** Matching ignores case, Ensembl version suffixes and reference-genome prefixes, so `GFAP`
  also finds mouse `Gfap` and `GRCh38_GFAP`. Matched names, as deposited, are returned per sample as
  `matched_features`.
- **Coverage.** Every name of a targeted panel (up to 10,000 features) is searchable, as is every name that
  appears in two or more panels. A name unique to one whole-transcriptome panel is not searchable.
- **`features` output.** `sda-db features` lists all features of a targeted panel. For a whole-transcriptome
  sample it shows the counts by feature type.
- **Local index.** `--feature-index PATH` or `SDA_DB_FEATURE_INDEX` selects a local index file.

## show

```bash
sda-db show GSM3036911          # sample card: identity, study, biology, assay, evidence, coordinates, image, QC
sda-db show GSE111672           # study: title, design, summary, PubMed IDs and its samples
```

## download

```bash
sda-db download GSM3036911 --dry-run
sda-db download GSE111672 --destination data/
sda-db download --from cohort.json --include-image --workers 8 --max-size 200GB --keep-going --report run.json
sda-db search lung --platform Xenium --format ids | sda-db download --from - --dry-run
```

- **Selection.** Nothing is selected unless you name IDs, `--from` or filters.
- **Plan.** `--dry-run` prints the total and per-sample bytes, what is already local, and the free space.
- **Size guard.** Every download is planned first, and the guard fails closed:
  - a plan above 20 GB stops unless you pass `--max-size` or `--yes`;
  - a plan whose sizes are unknown also stops, unless you pass `--yes`;
  - without dataset access the download stops with `access_denied`.
- **Integrity.** Every file is checked against the release SHA256 before it is installed. Existing verified
  files are reused, and differing local files are never overwritten.
- **Receipts.** `downloads/<commit>/.sda/receipts/<GSM>.json` records the source commit and the file hashes.
- **Failures.** `--keep-going` records failed samples and continues; otherwise the first failure stops the run.
- **Storage.** `--storage direct` writes files straight to the destination, without a second full-size copy in
  the Hugging Face cache.

## verify, inspect

```bash
sda-db verify downloads/999566bf516169ba8c9cf1ad9e5a2b722bb8a97a       # every sample under a commit
sda-db verify downloads/<commit>/samples/GSE111672/GSM3036911 --require-image
sda-db inspect GSM3036911       # downloaded sample: shape, feature types, coordinate range, QC, image levels
```

`verify` runs offline. `inspect` needs the `analysis` extra.

## cite

```bash
sda-db cite --from cohort.json -o refs.bib
sda-db cite GSE111672 --style text
```

The output lists the database paper, the release (with its commit) and one entry per GEO series, with
PubMed IDs and DOIs when the release records them.

## releases, doctor, mcp

```bash
sda-db releases --resolve-latest
sda-db doctor                   # Python, extras, release, token, login, public catalog, dataset access, disk
sda-db mcp --download-dir /data/sda --max-download-size 50GB
```

`doctor` never prints credentials. `mcp` is described in [AI agents and MCP](agents.md).
