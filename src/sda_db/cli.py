"""Command-line access to database catalogs and selected samples."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from .catalog import Catalog, DEFAULT_REVISION
from .download import download_sample, verify_sample

LOGGER = logging.getLogger(__name__)


def _catalog_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--revision", default=DEFAULT_REVISION, help="Commit, tag or branch; resolved once")
    parser.add_argument("--catalog-csv", type=Path, help="Read an existing CSV at --revision")
    parser.add_argument("--offline", action="store_true", help="Use only cached Hub files; needs full commit")
    parser.add_argument("--cache-dir", type=Path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return nonzero for lookup, network or integrity failures."""
    parser = argparse.ArgumentParser(prog="sda-db", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Filter the catalog without downloading matrices")
    _catalog_options(search)
    search.add_argument("--text", help="Literal substring across catalog metadata")
    search.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE", help="Repeat for AND filters")
    search.add_argument("--min-obs", type=int)
    search.add_argument("--max-obs", type=int)
    search.add_argument("--limit", type=int, default=20, help="Maximum displayed rows; exports include all matches")
    search.add_argument("--output", type=Path, help="Write the complete selection and fixed commit as JSON")
    download = commands.add_parser("download", help="Download specified GSMs and check SHA256")
    _catalog_options(download)
    download.add_argument("gsm", nargs="+")
    download.add_argument("--destination", type=Path, default=Path("downloads"))
    download.add_argument("--include-image", action="store_true", help="Also download image.tif when released")
    verify = commands.add_parser("verify", help="Verify an existing sample offline")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--require-image", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.command == "verify":
            print(json.dumps(verify_sample(args.directory, require_image=args.require_image), indent=2))
            return 0
        kwargs = dict(revision=args.revision, local_files_only=args.offline, cache_dir=args.cache_dir)
        catalog = (Catalog.from_csv(args.catalog_csv, **kwargs) if args.catalog_csv else Catalog.from_hub(**kwargs))
        if args.command == "search":
            if args.limit < 0:
                raise ValueError("--limit must be nonnegative")
            filters: dict[str, str | bool] = {}
            for item in args.where:
                key, separator, value = item.partition("=")
                if not separator or not key or not value:
                    raise ValueError("--where requires FIELD=VALUE")
                if key in filters:
                    raise ValueError(f"Duplicate filter: {key}")
                if key in {"has_image", "has_spatial_um"}:
                    if value.casefold() not in {"true", "false"}:
                        raise ValueError(f"{key} requires true or false")
                    filters[key] = value.casefold() == "true"
                else:
                    filters[key] = value
            result = catalog.search(args.text, min_obs=args.min_obs, max_obs=args.max_obs, **filters)
            if args.output:
                result.save_selection(args.output)
            print(json.dumps({"repo_id": catalog.repo_id, "revision": catalog.revision,
                              "matches": len(result.rows), "shown": min(args.limit, len(result.rows)),
                              "samples": result.rows[:args.limit]}, ensure_ascii=False, indent=2))
        else:
            # Validate every ID first, so a typo cannot start a partial batch.
            for gsm in args.gsm:
                catalog.get(gsm)
            for gsm in dict.fromkeys(args.gsm):
                print(download_sample(catalog, gsm, args.destination, include_image=args.include_image))
        return 0
    except Exception as exc:
        # Hub exceptions may include URLs and credentials; report only their type.
        message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError, ImportError)) else type(exc).__name__
        LOGGER.error("%s", message)
        return 1
