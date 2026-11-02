# Access and troubleshooting

**Searching needs no account.** The catalog of the paper release (`v1.0.0-20261025`) is mirrored, byte for
byte, in the ungated [`Biogod/SpatialDataAgent-DB-Catalog`](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB-Catalog).
The client checks each mirrored file against the SHA256 recorded for the release.

**Sample files need approval.** They are in
[`Biogod/SpatialDataAgent-DB`](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB), where access
requests are reviewed manually and need a verified institutional e-mail in your Hugging Face account:

1. Request access with your own account on the dataset page.
2. After approval, run `hf auth login`, or set `HF_TOKEN`.
3. Run `sda-db doctor`. It checks the token, login, public catalog, dataset access and free disk, and prints
   a fix for each failure. It never prints credentials.

The toolkit uses the official `huggingface_hub` APIs. Credentials stay in the Hub's local store or in
`HF_TOKEN`; the client never writes them into selections, receipts or reports. See
[Hub authentication](https://huggingface.co/docs/huggingface_hub/quick-start#authentication).

| Symptom | Action |
|---|---|
| GatedRepoError / 401 / 403 | Open the database page; confirm approved access and the account used by `hf auth whoami` |
| RepositoryNotFoundError | Check repository name and authenticated access; private/gated resources may appear missing |
| GSM absent | Search the pinned catalog and inspect aliases; a withdrawn GSM may remain only in historical commits |
| SHA256 mismatch | Keep the failed file for inspection; retry to a new destination using the same commit |
| Existing file has different bytes | The client preserves your file; select a new destination or inspect it manually |
| No recorded micrometre coordinates | Inspect `coordinate_units` and `um_basis`; use native units where appropriate |
| No released image | Search with `has_image=true`; `--include-image` cannot create missing images |
| Offline cache miss | Download online at the same full commit first; `--offline` cannot fetch missing files |
| Network timeout / exit 6 | Check the network, proxy variables and `HF_ENDPOINT`; keep TLS verification. Cached releases work with `--offline` |
| Disk quota exceeded / exit 8 | Free space, choose another `--destination`, or move the Hub cache with `--cache-dir` or `HF_HOME` |
| HTTP 429 | The Hub rate-limited the client. Log in, lower `--workers`, wait and retry |
| Exit 7 (size limit) | Inspect sizes with `--dry-run`, then narrow the selection, omit images or raise `--max-size` |
| ImportError for AnnData / tifffile | `pip install 'spatial-data-agent-db[analysis]'` |

`--cache-dir PATH` selects the Hub cache. Environment configuration such as
`HF_HOME` should be set before launching Python. Images are downloaded only with
`--include-image`; the default expression download can still be large.

For support, open an issue in this GitHub repository with the client version,
database commit, GSM, command (without credentials) and error type. Do not post
tokens, signed download URLs or private configuration.
