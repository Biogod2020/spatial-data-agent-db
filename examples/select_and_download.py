"""Inspect and download an explicitly bounded subset, without model calls."""

from __future__ import annotations

import argparse

from sda_db import Catalog, DEFAULT_REVISION, download_sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--platform", default="Visium")
    parser.add_argument("--organism", default="Homo sapiens")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--download", action="store_true", help="Without this flag, inspect only")
    parser.add_argument("--include-image", action="store_true")
    parser.add_argument("--destination", default="downloads")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    catalog = Catalog.from_hub(revision=args.revision)
    selected = catalog.search(platform=args.platform, organism=args.organism, split="train")
    selected.save_selection("selection.json")
    print(f"{len(selected.rows)} matches at commit {selected.revision}")
    for row in selected.rows[:args.limit]:
        print(row["gsm"], row["n_obs"], row["n_vars"], row["expression_state"], row["has_image"])
        if args.download:
            print(download_sample(selected, row["gsm"], args.destination, include_image=args.include_image))


if __name__ == "__main__":
    main()
