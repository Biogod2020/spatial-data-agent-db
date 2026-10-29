# Version 0.1.0 validation

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
