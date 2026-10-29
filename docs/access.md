# Access and troubleshooting

The published data source is
[`Biogod/SpatialDataAgent-DB`](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB).
This GitHub toolkit does not grant database access. If the dataset requires
approval, request access and accept its terms in a browser, then run `hf auth login`
with the approved account. Use credentials with dataset read access.

The toolkit uses official `huggingface_hub` APIs. Login credentials remain in the
Hub's local store or `HF_TOKEN`; the client does not write them into selections
or receipts. See [Hub authentication](https://huggingface.co/docs/huggingface_hub/quick-start#authentication)
and [file downloads](https://huggingface.co/docs/huggingface_hub/package_reference/file_download).

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
| Network timeout | Check the local network and standard Hub configuration; retain TLS verification |
| ImportError for AnnData | Install `python -m pip install -e '.[analysis]'` |

`--cache-dir PATH` selects the Hub cache. Environment configuration such as
`HF_HOME` should be set before launching Python. Images are downloaded only with
`--include-image`; the default expression download can still be large.

For support, open an issue in this GitHub repository with the client version,
database commit, GSM, command (without credentials) and error type. Do not post
tokens, signed download URLs or private configuration.
