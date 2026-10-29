"""Read a bounded selection while prefetching and recycling sample cache files."""

from __future__ import annotations

import argparse

from sda_db import Catalog, DEFAULT_REVISION, stream_samples
from sda_db.io import read_sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--platform", default="Visium")
    parser.add_argument("--organism", default="Homo sapiens")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--cache-dir", default=".sda-cache")
    parser.add_argument("--cache-gib", type=float, default=10)
    parser.add_argument("--prefetch", type=int, default=1)
    parser.add_argument("--include-image", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.cache_gib <= 0:
        parser.error("--limit and --cache-gib must be positive")
    selected = Catalog.from_hub(revision=args.revision).search(platform=args.platform,
                                                              organism=args.organism, split="train")
    gsms = [row["gsm"] for row in selected.rows[:args.limit]]
    with stream_samples(selected, gsms, cache_dir=args.cache_dir, max_cache_bytes=int(args.cache_gib * 1024**3),
                        prefetch=args.prefetch, include_image=args.include_image) as samples:
        for sample in samples:
            adata, meta = read_sample(sample.directory, verify=False)
            try:
                print(sample.gsm, adata.shape, meta["expression"]["state"])
                print(samples.cache_info)
            finally:
                adata.file.close()


if __name__ == "__main__":
    main()
