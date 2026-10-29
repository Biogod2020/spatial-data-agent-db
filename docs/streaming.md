# Sample streaming and bounded caching

Version 0.2 adds **sample-at-a-time iteration**. Each sample is downloaded and
SHA256-verified before delivery, while a background worker prefetches subsequent
samples. You can analyze more samples than fit on local disk, provided each
individual requested sample fits within the cache budget.

```python
from sda_db import Catalog, stream_samples
from sda_db.io import read_sample

catalog = Catalog.from_hub()
selected = catalog.search(platform="Visium", organism="Homo sapiens", split="train")

with stream_samples(selected, cache_dir=".sda-cache", max_cache_bytes=10 * 1024**3,
                    prefetch=1) as samples:
    for sample in samples:
        adata, metadata = read_sample(sample.directory, verify=False)
        try:
            # This sample was already verified before delivery. Inspect metadata
            # and process only the expression blocks needed for your analysis.
            print(sample.gsm, adata.shape, metadata["expression"]["state"])
        finally:
            adata.file.close()
        # Advancing releases this sample. Its cache files may then be evicted.
```

For a bounded runnable example, see [stream_samples.py](../examples/stream_samples.py).
The reader uses `backed='r'`; coordinates and other AnnData annotations can still
occupy memory. The SDK does not automatically preprocess expression or merge studies.

## API and lifetimes

`stream_samples(catalog, gsms=None, cache_dir='.sda-cache',
max_cache_bytes=10*1024**3, prefetch=1, include_image=False)` returns a
`SampleStream`. Use its context manager so early breaks and exceptions release
the worker and cache lock. `gsms=None` iterates the catalog selection in order;
an explicit list restricts the stream. Duplicate IDs are processed once and
missing IDs fail before any download starts.

Each `StreamSample` exposes `gsm`, `row` and `directory`. Its files remain pinned
until the iterator advances, `sample.release()` is called, or the stream closes.
Close all backed AnnData/TIFF handles first. After release, accessing
`sample.directory` raises; previously saved `Path` values may no longer exist.
`with sample:` can be used to release earlier within a loop.
Use `download_sample` outside the cache for permanent outputs.

One worker overlaps network transfer with consumer computation. `prefetch=1`
keeps at most one queued/ready sample ahead of the consumer; values 0–4 are
supported. Zero disables background work. Downloads remain ordered and a failed
sample raises at its position instead of silently skipping it.

`samples.cache_info` reports `max_bytes`, `allocated_bytes`, `ready_bytes`,
`reserved_bytes`, `cached_samples` and `pinned_samples`.

## Cache capacity

Before downloading a new sample, the SDK requests file sizes at the same pinned
Hub commit and reserves the combined size of `sample.json`, `spatial.h5ad` and
the optional requested `image.tif`. A sample larger than `max_cache_bytes` raises
`CacheLimitError` before its payload is transferred. Increase the limit, omit
images, or use a permanent download for that sample.

The capacity covers **sample payload bytes**, including reservations for active
transfers. Small Hub bookkeeping files, cache records, download receipts and
filesystem allocation overhead are additional. Allow some free disk headroom;
the setting does not govern the separate catalog/Hub caches or Xet's own global
transfer configuration.

Released entries are evicted by least recent use. Current and prefetched entries
are pinned, so prefetch waits when they occupy the budget; advancing or releasing
the current sample allows eviction and wakes the worker. The cache need only fit
the largest individual sample, though more room can improve overlap.

Use a dedicated empty cache directory. The SDK marks and owns this cache and
will delete its disposable entries. It rejects nonempty unmarked directories.
One stream exclusively owns a cache directory at a time; another process or
stream receives a `filelock.Timeout` error instead of evicting files in use.
Use separate directories for independent concurrent consumers.

## Reuse, errors and shutdown

Completed entries persist between streams, are hash-checked before reuse and
keep their source commit and receipts. An expression-only cache entry can be
upgraded to include an image without downloading the expression again.
With `catalog.local_files_only=True`, streaming uses completed entries in this
managed cache and makes no Hub requests. A missing entry raises immediately;
this mode does not import arbitrary files from the separate global Hub cache.

Failed transfers never reach the consumer. Their disposable cache entry is
removed, and interrupted partial entries are cleaned on the next cache open.
The Hub handles transport retries; partially transferred cache entries are not
promised to survive a failed stream for cross-process resume. Permanent downloads
retain their own staging area for later retries.

On early exit, queued work is cancelled and capacity waits are awakened. An
already active Hub file transfer is allowed to finish before its unfinished
sample is cleaned and the cache lock is released. The SDK does not forcibly kill
network threads or interrupt an active transfer midway.

## Direct permanent downloads

The same direct transfer mode can avoid a second full-size Hub-cache copy for
permanent downloads:

```python
from sda_db import download_sample
directory = download_sample(catalog, "GSM3036911", "downloads", storage="direct")
```

```bash
sda-db download GSM3036911 --destination downloads --storage direct
```

Direct mode stages transfers on the destination filesystem, verifies SHA256,
installs the verified file by hard link and removes its staging name. This
requires a filesystem that supports hard links, as the original downloader does.
It preserves existing conflicting files. Repeated calls use recorded local
metadata and verified assets without requesting them again. The existing default
`storage='hub'` still downloads through the shared Hub cache and copies files to
the destination. Managed streaming always uses direct mode.

This implementation delivers complete verified samples. File-internal remote
H5AD slices, gene requests and TIFF tile access are separate future capabilities.
