# Client validation

## Version 0.4.0

Validated on 2026-11-05 against `v1.1.0-20261105` (paper commit `999566bf516169ba8c9cf1ad9e5a2b722bb8a97a` plus
metadata v2, public catalog `0ddfdbfeb5e19e6d35fcfa8fe46596631675e28e`, gated tag `metadata-v2.1-20261105` =
`1c0b636ee1b2974bf8f8ebd963730a2b35b625cd`; metadata version 2.1, which unifies `assay_name` and copies the v3
micrometre values exactly), the paper release `v1.0.0-20261025`, and `latest` (`1c0b636`). All nine `metadata/v2`
files were hashed after download from the public commit; the gated table has the same SHA256.

**Offline tests.** 136 tests passed with socket connections disabled: the 114 tests of 0.3.2 plus 22 for
metadata v2. Two 0.3.2 tests were updated because the default release changed: they now check the paper
release by name. The new tests cover the gsm join (v3 fields kept, `platform_v3`/`organism_v3`/`sex_v3`),
each new filter and its aliases, `min_confidence` and `exclude_flags`, keyword ranking over v2 fields, facets,
cards, citations, selections, CLI flags and MCP arguments (still 13 tools), SHA256 mismatches, incomplete or
inconsistent v2 tables, and the paper release staying free of v2 fields.

**Paper release reproducibility.** Against the live Hub, client 0.3.2 and 0.4.0 give the same `--release paper`
output: SHA256 of all rows `21293b3b…`, of the ranked result and scores for "human lung fibrosis Visium"
`dd74575b…`, of `cite()` `438a35ef…` and of `fields()` `65e0df50…`.

**Live acceptance.** `tests/live_acceptance.py` passed 11 of 11 checks in 14.7 s (Python 3.12.13, Hub 1.33.0). Metadata v2 search without a token ("human lung fibrosis Visium", organ lung, minimum confidence
0.6) returned 81 samples. The default release reports 204 corrected platforms; downloads, receipts, image
patches, the MCP session and `latest` passed unchanged.

**Live searches on the default release** (samples / studies): organ lung + disease "fibrosis" 105 / 10;
`MONDO:0004975` 253 / 23; organism `mouse`, `house mouse` or `10090` 5,053 / 579 (with xenograft hosts);
`min_confidence=0.6` 9,044; `exclude_flags=True` 8,999; `assay_type=imaging` 3,416 / 332; 35 distinct
`assay_name` values (`Molecular Cartography` 70, `Visium` 6,505).

## Version 0.3.0

Validated on 2026-11-02 against the paper release `v1.0.0-20261025` (commit
`999566bf516169ba8c9cf1ad9e5a2b722bb8a97a`) and `latest` (`e1ba100b8e21d5e966c71a478886287647ccb870`).

**Offline tests.** 113 tests passed with socket connections disabled: the 45 tests of 0.2, unchanged in what
they assert, plus 68 new ones, four of them for gene search. Twelve of the new tests are regression tests for confirmed findings of an
independent code review. The new tests cover:

- ranking, phrases, exclusions, prefixes, aliases, operators, typo errors and suggestions;
- facets, studies, cards, exports and selection replay;
- citations;
- release resolution and mirror hash binding;
- size plans, size limits, and parallel downloads in both error modes;
- error translation, including over-quota disks;
- the CLI JSON contract and exit codes;
- the MCP protocol (negotiation, tool schemas, results, notifications, batches, the stdio loop);
- AnnData loading and pyramidal TIFF reads, on a synthetic sample.

**Packaging.** A wheel built from this tree was run with `uvx` in a clean environment. It contains the
release registry and field documentation. `sda-db releases` and an ungated search worked.

**Live acceptance.** `tests/live_acceptance.py` ran in 13 s, and again in 9.9 s after the review fixes. Every check passed:

| Check | Result |
|---|---|
| Search without a token (`HF_TOKEN` unset, implicit token disabled, empty cache) | passed; catalog read from `Biogod/SpatialDataAgent-DB-Catalog@2ae26e0`, the three catalog files matched the recorded SHA256 |
| Paper release | 12,163 samples, 1,265 published series |
| Download plan | 2 samples with images (GSM9448988 Xenium DAPI, GSM8915497 Visium H&E): 10,072,950 bytes from the release file manifest |
| Parallel download with `max_bytes="20MB"` | 2 downloaded, 0 failed; receipts verified offline with `require_image=True` |
| `load` and `read_patch` | AnnData 1,142 × 17,943; 64 × 64 × 3 H&E patch centred on the first spot |
| `sda-db mcp` over stdio | protocol 2025-06-18 negotiated, 12 tools, ranked search (262 matches), a 214.6 GB Xenium download refused with `size_limit` |
| `latest` | resolved to `e1ba100`; 12,232 samples, including 69 reviewed daily additions |

**Feature index (gene search).**

- **Build.** `tools/build_feature_index.py` read the `var` table of every released `spatial.h5ad` of the paper
  release. It ran on a compute node and took 707 s.
  - Before reading, all 12,163 file sizes matched the release file manifest, and 60 randomly chosen files
    re-hashed to the catalog SHA256. There were 0 problems.
  - The result is 12,163 samples in 1,954 distinct panels, with 35,045,457 feature rows.
- **Conversion.** `tools/compact_feature_index.py` wrote the SQLite index (schema `sda-db-feature-index-3`).
  It is 41.9 MB gzip-compressed and 184 MB unpacked, with SHA256 `c2376d37…2d4`.
  - 1,703,058 of 4,488,042 search keys are searchable: every key of a targeted panel (up to 10,000
    features), and every key found in two or more panels.
- **Speed and memory.** On the login node, gene queries took 0.01–0.07 s, and the process peaked at 0.13 GB.
  For comparison, a JSON prototype needed 2.6–3.7 GB, so it was replaced.
- **Results.**
  - `GFAP` was measured in 8,314 samples from 971 studies. `GFAP` + `AQP4` gave 110 human Xenium samples
    from 8 studies.
  - `ENSG00000131095` resolved to the deposited symbol `GFAP`.
  - An unknown symbol produced a warning.
- **Correctness.** The stored lists of 25 random targeted panels were identical to their `var` tables
  (25/25). 200 of 200 random feature names from 10 whole-transcriptome samples were found for their sample.
- **Publication.** The index was published on 2026-11-03 to `Biogod/SpatialDataAgent-DB-Catalog` (commit
  `080eaea`), with `catalog/feature-index-provenance.json`, and recorded in `releases.json`.
- **Fresh install test.** A user with no token and an empty cache ran `sda-db search --gene GFAP --gene AQP4
  --organism human --platform Xenium`. It returned 110 samples in 8 studies, with the 42 MB download taking
  about 18 s; later queries took about 0.5 s, including process start.

**Live acceptance after gene search.** `tests/live_acceptance.py` now also checks gene search without a
token. It passed 9 of 9 checks in 19.1 s, including the first index download. `--gene GFAP --gene AQP4
--platform Xenium` matched 596 samples, with deposited names such as `Gfap`/`Aqp4`, and the MCP session
listed 13 tools.

**Further manual checks.**

- **MCP interoperability.** The official MCP Python client (`mcp` 2.3.0) connected over stdio and negotiated
  protocol 2025-11-25. All 12 tools returned results; typo and unknown-argument errors came back as tool
  errors with hints.
- **No-token experience.** `sda-db doctor` reported the missing dataset approval with the fix. A download
  exited with code 4 (`access_denied`) and the same hint.
- **Approved account.** `doctor` reported every check as passing.
- **Search quality.** "alzheimer" matched 452 samples in 27 studies (0 in 0.2), and `platform=visum` was
  answered with `'Visium', 'Visium HD'`.
- **Disk quota.** A real over-quota home directory produced `[Errno 122]`. The client now reports it as
  `disk_full` (exit 8), with a hint to move the cache.

**Environment.** Python 3.11.16, huggingface_hub 1.33.0, anndata 0.12.19, NumPy 2.4.6, tifffile 2026.3.3 and
zarr 3.1.6, using the standard Hub login and a local proxy. Downloads stayed bounded at about 30 MB in total.
The validation published nothing to the dataset, made no model calls and changed no scientific labels.

## Version 0.2.0

Validated on 2026-10-29, with the same database commit as version 0.1 below.

- **45 offline tests passed** with socket connections disabled. Added coverage
  includes ordered delivery, overlapping prefetch, bounded queueing, capacity
  backpressure, active leases, LRU eviction, oversized-sample admission, image
  upgrades, offline reuse, exclusive cache ownership, shutdown during a capacity
  wait and active transfer, corruption, fixed-commit HEAD checks, size mismatches
  and direct installation without an SDK payload copy.
- The 0.2 wheel built and installed independently of the main repository and
  optional analysis packages. Its 41 core tests passed, with four NumPy coordinate
  tests skipped in the minimal environment. The CLI exposes `--storage direct`.
- A bounded live read delivered **two real samples**, with a total payload of
  **24,639,264 bytes**, under a **16,258,695-byte** sample-payload cache budget.
  Both samples passed SHA256 and receipt verification. The first was evicted
  after release; the second was reused offline from the managed cache.
- At the end, the managed cache held one expression file and the dedicated Hub
  cache held no expression-file snapshot. No full-size copy remained in direct
  staging. The retained sample was also read as backed CSR AnnData.

| Sample | Metadata bytes | Expression bytes |
|---|---:|---:|
| GSE111672 / GSM3036911 | 3,834 | 16,254,861 |
| GSE111672 / GSM3405534 | 3,845 | 8,376,724 |

Live access used Hub 1.10.1 with the existing approved read credential and the
host's registered direct DNS/TLS transport in an isolated child. The independent
installation used Hub 1.33.0. The transport/configuration and downloaded assets
remain outside Git. This was a functional cache/eviction check, not a throughput
benchmark; image transport remained covered by synthetic tests. No model calls,
database publication or scientific reclassification occurred.

The payload budget excludes small receipts, Hub bookkeeping and filesystem
allocation overhead. An active Hub file is allowed to finish during stream
shutdown. See [streaming behavior](streaming.md) for these operational details.

## Version 0.1.0

Validated on 2026-10-29. The checks below concern the user client; no new
classification, standardization, human review or database publication was run.

| Check | Result |
|---|---|
| Offline client and coordinate tests | 21 passed, with network connections disabled |
| Independent wheel build and installation | Passed; installed CLI works without the main repository or analysis dependencies |
| Tests from the installed wheel | 17 core tests passed; 4 optional NumPy coordinate tests skipped in the minimal environment |
| Catalog parsing against local historical release | All 12,163 rows parsed with typed booleans and numeric fields |
| Fixed-commit live Hub catalog | All 12,163 rows parsed; recorded CSV SHA256 below |
| Live metadata selection | Human Visium samples with images in train split: 2,756 matches |
| Live single-sample download | `GSE111672/GSM3036911`: metadata and expression hashes passed |
| Repeated download | Existing verified matrix reused |
| Offline Hub cache | Same pinned catalog and sample retrieved with `local_files_only=True` |
| Real AnnData reading | Backed CSR expression, shape 956 × 25,753, read successfully |
| Local documentation links | All local Markdown targets resolve |

The 21 tests cover boolean decoding, exact GSE membership, inclusive observation
bounds, invalid filters, missing IDs, duplicate rows, path traversal and symlink
escapes, one-time reference resolution, offline mode, image opt-in, corruption,
existing-file preservation, metadata/catalog conflicts, receipt tampering,
selection exports and coordinate transforms. Synthetic test payloads are not
scientific sample fixtures.

Live read source:

```text
repo: Biogod/SpatialDataAgent-DB
commit: 8f116b867ce4097957d4209db712d37bae3c9369
catalog/samples.csv SHA256:
58085c78174d541d878147912c97ef8d41eaaf87043bbf0c69c2925bbb5ea2cc
samples/GSE111672/GSM3036911/sample.json:
3,834 bytes
5dc92ae05dce178104de9bbd03e6c8989f10bea0d086af2d3741e19966bb02cf
samples/GSE111672/GSM3036911/spatial.h5ad:
16,254,861 bytes
6a085d7c8141fe5a6e3f1d441371840a969c1a3c70cf5bc202369f2a76671994
```

The live check used the existing approved read credential and host-specific
authenticated DNS/TLS transport in an isolated process. That adapter and its
configuration are not part of this portable client. General users use standard
Hub authentication and network configuration. Live Hub access used Hub 1.10.1;
the independent installation used Hub 1.33.0. AnnData reading used 0.12.19 and
NumPy 2.2.6. Python 3.11 was tested locally; the CI workflow additionally targets
3.12 and 3.13, whose results are not claimed here.

The live test downloaded no tissue image and did not measure large-batch or
network throughput. Image hashes and transforms were covered by offline tests.
No database writes or model calls were made. Downloaded files, credentials and
host-specific test scripts are excluded from Git.
