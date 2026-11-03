"""sda-db: find, plan, download, verify, read and cite SpatialDataAgent-DB samples.

Output is a table on a terminal and JSON when piped (or with --json), so the same
commands serve people, scripts and AI agents. Errors carry a stable code and a hint.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable, Sequence

from .catalog import DEFAULT_REVISION, SUMMARY_FIELDS, Catalog
from .download import verify_sample
from .errors import ACCESS_HINT, AccessError, NotFoundError, SdaDbError, SizeLimitError, translate

LOGGER = logging.getLogger("sda_db")
TABLE_FIELDS = ("gsm", "published_gse", "platform", "organism", "tissue", "disease", "n_obs", "image_stain",
                "split", "study_title")
DEFAULT_GUARD_BYTES = 20 * 10**9
ID = re.compile(r"(GSM|GSE)[0-9]+\Z")
EPILOG = """examples:
  sda-db search alzheimer hippocampus --organism human --has-image
  sda-db search --platform "visium hd" --where "n_obs>=50000" --output cohort.csv
  sda-db values tissue --organism mouse --top 20
  sda-db search --platform Xenium --gene GFAP --gene AQP4 --organism human
  sda-db features GSM9448988 --contains CD
  sda-db show GSM3036911 GSE111672
  sda-db download GSM3036911 --dry-run
  sda-db search lung --platform Xenium --format ids | sda-db download --from - --max-size 50GB
  sda-db cite --from cohort.json -o refs.bib
  sda-db doctor

Docs: https://github.com/Biogod2020/spatial-data-agent-db"""


# --------------------------------------------------------------------------- output
def _json_mode(args: argparse.Namespace) -> bool:
    fmt = getattr(args, "format", None)
    if getattr(args, "json", False) or fmt == "json":
        return True
    if fmt is not None:
        return False
    return not sys.stdout.isatty()


def _emit(value: Any) -> None:
    pretty = sys.stdout.isatty()  # compact when piped: fewer tokens for agents, same content
    print(json.dumps(value, ensure_ascii=False, default=str, indent=2 if pretty else None,
                     separators=None if pretty else (",", ":")))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, tuple)) and all(not isinstance(v, (list, dict)) for v in value):
        return ", ".join(_cell(v) for v in value)
    return " ".join(str(value).split())


def _table(records: list[dict[str, Any]], columns: Sequence[str]) -> str:
    if not records:
        return ""
    width = shutil.get_terminal_size((160, 20)).columns
    cells = [[_cell(r.get(c)) for c in columns] for r in records]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    caps = [min(w, 48) for w in widths]
    while sum(caps) + 2 * (len(caps) - 1) > width and max(caps) > 12:
        caps[caps.index(max(caps))] -= 1
    fit = lambda text, n: text if len(text) <= n else text[: max(1, n - 1)] + "…"  # noqa: E731
    lines = ["  ".join(fit(c, caps[i]).ljust(caps[i]) for i, c in enumerate(columns)).rstrip()]
    lines += ["  ".join(fit(row[i], caps[i]).ljust(caps[i]) for i in range(len(columns))).rstrip() for row in cells]
    return "\n".join(lines)


def _print_records(args: argparse.Namespace, records: list[dict[str, Any]], columns: Sequence[str]) -> None:
    fmt = getattr(args, "format", None) or "table"
    if fmt in {"csv", "tsv"}:
        import csv

        writer = csv.DictWriter(sys.stdout, fieldnames=list(columns), delimiter="," if fmt == "csv" else "\t",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: ("" if r.get(k) is None else r.get(k)) for k in columns} for r in records)
    elif fmt == "jsonl":
        for record in records:
            print(json.dumps(record, ensure_ascii=False, default=str))
    elif fmt == "ids":
        for record in records:
            print(record.get("gsm") or record.get("published_gse"))
    else:
        print(_table(records, columns))


def _note(text: str) -> None:
    print(text, file=sys.stderr)


# --------------------------------------------------------------------------- parsers
def _catalog_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("release")
    group.add_argument("--release", "--revision", dest="release", default=None, metavar="NAME",
                       help="release name, alias, tag, 'latest' or full commit (default: the paper release)")
    group.add_argument("--source", choices=["auto", "public", "gated"], default="auto",
                       help="where catalog files come from; auto uses the ungated mirror when the release has one")
    group.add_argument("--offline", action="store_true", help="use only cached files")
    group.add_argument("--cache-dir", type=Path, help="Hugging Face cache directory")
    group.add_argument("--catalog-csv", type=Path, help=argparse.SUPPRESS)


def _output_options(parser: argparse.ArgumentParser, formats: Sequence[str] = ("table", "json")) -> None:
    group = parser.add_argument_group("output")
    group.add_argument("--json", action="store_true", help="JSON output (default when stdout is not a terminal)")
    group.add_argument("--format", choices=list(formats), help="output format")


def _filter_options(parser: argparse.ArgumentParser, *, positional_query: bool = False) -> None:
    group = parser.add_argument_group("selection (all conditions are ANDed)")
    if positional_query:
        group.add_argument("query", nargs="*", help="keywords over tissue, disease, organism, platform, IDs and "
                                                     "study title/summary; \"quoted phrase\", -exclude, prefix*")
    else:
        group.add_argument("-q", "--query", help="keywords, as for 'sda-db search'")
    group.add_argument("--platform", action="append", help="exact platform (aliases like 'visium hd'); repeat = OR")
    group.add_argument("--organism", action="append", help="organism, scientific or common name; repeat = OR")
    group.add_argument("--tissue", action="append", help="tissue substring, case-insensitive; repeat = OR")
    group.add_argument("--disease", action="append", help="disease substring, case-insensitive; repeat = OR")
    group.add_argument("--gse", action="append", help="series accession (any membership); repeat = OR")
    group.add_argument("--split", action="append", choices=["train", "val", "test"], help="release split")
    group.add_argument("--stain", action="append", help="exact image stain, e.g. 'H&E' or 'fluorescence: DAPI'")
    image = group.add_mutually_exclusive_group()
    image.add_argument("--has-image", dest="has_image", action="store_true", default=None,
                       help="only samples with a released image")
    image.add_argument("--no-image", dest="has_image", action="store_false", help="only samples without an image")
    group.add_argument("--min-obs", type=int, help="at least this many observations")
    group.add_argument("--max-obs", type=int, help="at most this many observations")
    group.add_argument("--where", action="append", default=[], metavar="EXPR",
                       help="FIELD=V, FIELD!=V, FIELD~TEXT, FIELD>=N ...; '|' = OR; repeat = AND")
    group.add_argument("--gene", action="append", metavar="GENE",
                       help="only samples that measure this gene/feature (symbol or ID, any case); repeat = AND")
    group.add_argument("--gene-any", action="store_true", help="with several --gene: measuring any of them")
    group.add_argument("--feature-index", type=Path, help="local feature index file (default: the release's)")
    group.add_argument("--any", action="store_true", help="keywords match if any term matches (default: all)")
    group.add_argument("--text", help="literal substring over all catalog values")
    group.add_argument("--sort", help="sort by FIELD (prefix '-' for descending), e.g. -n_obs")


def _id_options(parser: argparse.ArgumentParser, *, positional: bool = True) -> None:
    if positional:
        parser.add_argument("ids", nargs="*", metavar="ID", help="GSM or GSE accessions")
    parser.add_argument("--from", dest="from_file", metavar="FILE",
                        help="selection JSON (from --output), a file of IDs, or '-' for IDs on stdin")


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(prog="sda-db", description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"sda-db {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log HTTP and cache activity")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    search = commands.add_parser("search", help="find samples by keywords and filters (no download)",
                                 description="Search the catalog. Ranked when keywords are given.")
    _filter_options(search, positional_query=True)
    search.add_argument("--limit", type=int, default=None,
                        help="rows shown (default 20 for table/json, all for ids/csv/tsv/jsonl); exports keep all")
    search.add_argument("--all", action="store_true", help="show every match")
    search.add_argument("--fields", default=None,
                        help="comma-separated fields to show, 'summary' (default) or 'all'")
    search.add_argument("--studies", action="store_true", help="one row per study instead of per sample")
    search.add_argument("--output", "-o", type=Path,
                        help="save the complete selection: .json (replayable), .csv, .tsv, .jsonl or .parquet")
    _catalog_options(search)
    _output_options(search, ("table", "json", "jsonl", "csv", "tsv", "ids"))

    fields = commands.add_parser("fields", help="list searchable fields, their meaning and top values")
    _catalog_options(fields)
    _output_options(fields)

    values = commands.add_parser("values", help="distinct values of a field, with counts")
    values.add_argument("field", help="catalog field, e.g. platform, tissue, disease, image_stain")
    values.add_argument("--top", type=int, default=50, help="values shown (default 50; 0 = all)")
    _filter_options(values)
    _catalog_options(values)
    _output_options(values, ("table", "json", "csv", "tsv"))

    summary = commands.add_parser("summary", help="counts for a selection, optionally by fields")
    summary.add_argument("--by", help="comma-separated fields to cross-tabulate, e.g. platform,organism")
    _filter_options(summary)
    _catalog_options(summary)
    _output_options(summary, ("table", "json", "csv", "tsv"))

    show = commands.add_parser("show", help="everything about samples (GSM) or studies (GSE)")
    show.add_argument("ids", nargs="+", metavar="ID", help="GSM or GSE accessions")
    _catalog_options(show)
    _output_options(show)

    download = commands.add_parser("download", help="plan or download verified samples",
                                   description="Select by IDs, --from and/or filters; then plan or download. "
                                               "Nothing is selected unless you ask for it.")
    _id_options(download)
    _filter_options(download)
    download.add_argument("--destination", "-d", type=Path, default=Path("downloads"))
    download.add_argument("--include-image", action="store_true", help="also fetch image.tif when released")
    download.add_argument("--dry-run", action="store_true", help="show sizes and stop")
    download.add_argument("--max-size", help="refuse if more than this must be fetched, e.g. 50GB")
    download.add_argument("--yes", "-y", action="store_true",
                          help=f"skip the {DEFAULT_GUARD_BYTES // 10**9} GB safety limit")
    download.add_argument("--workers", type=int, default=4, help="parallel samples (default 4)")
    download.add_argument("--keep-going", action="store_true", help="continue after a failed sample")
    download.add_argument("--storage", choices=["hub", "direct"], default="hub",
                          help="direct avoids a second full-size copy in the Hub cache")
    download.add_argument("--report", type=Path, help="write the download report JSON here")
    _catalog_options(download)
    _output_options(download)

    verify = commands.add_parser("verify", help="re-check downloaded samples offline")
    verify.add_argument("directory", type=Path, nargs="+",
                        help="sample directories, or a downloads/<commit> folder to check every sample in it")
    verify.add_argument("--require-image", action="store_true")
    _output_options(verify)

    inspect = commands.add_parser("inspect", help="describe a downloaded sample (needs the analysis extra)")
    inspect.add_argument("target", help="sample directory, or a GSM already under --destination")
    inspect.add_argument("--destination", "-d", type=Path, default=Path("downloads"))
    inspect.add_argument("--verify", action="store_true", help="re-hash files before reading")
    _catalog_options(inspect)
    _output_options(inspect)

    features = commands.add_parser("features", help="genes/features measured in a sample (no download)")
    features.add_argument("gsm")
    features.add_argument("--contains", help="only features whose name or ID contains this text")
    features.add_argument("--limit", type=int, default=50, help="features shown (default 50; 0 = all)")
    features.add_argument("--feature-index", type=Path, help="local feature index file (default: the release's)")
    _catalog_options(features)
    _output_options(features, ("table", "json", "csv", "tsv"))

    cite = commands.add_parser("cite", help="citations for the database and the original studies")
    _id_options(cite)
    _filter_options(cite)
    cite.add_argument("--output", "-o", type=Path, help="write to a file (.bib)")
    cite.add_argument("--style", choices=["bibtex", "text", "json"], default="bibtex")
    _catalog_options(cite)

    releases = commands.add_parser("releases", help="database releases this client knows")
    releases.add_argument("--resolve-latest", action="store_true", help="look up the current 'latest' commit online")
    _output_options(releases)

    doctor = commands.add_parser("doctor", help="check Python, network, login and dataset access")
    doctor.add_argument("--release", default=None)
    doctor.add_argument("--offline", action="store_true", help="skip network checks")
    doctor.add_argument("--destination", "-d", type=Path, default=Path("downloads"))
    _output_options(doctor)

    mcp = commands.add_parser("mcp", help="run the Model Context Protocol server for AI agents (stdio)")
    mcp.add_argument("--release", default=None, help="default release for tools")
    mcp.add_argument("--download-dir", type=Path, help="where download_samples writes (default: $SDA_DB_DOWNLOAD_DIR "
                                                       "or the user cache)")
    mcp.add_argument("--max-download-size", default=None, help="per-call download limit (default 20GB)")
    mcp.add_argument("--allow-downloads", action=argparse.BooleanOptionalAction, default=True,
                     help="expose the download tool (default on; it still plans first)")
    return parser


# --------------------------------------------------------------------------- helpers
def open_catalog(args: argparse.Namespace, *, release: str | None = None) -> Catalog:
    chosen = release or args.release
    if getattr(args, "catalog_csv", None):
        return Catalog.from_csv(args.catalog_csv, revision=chosen or DEFAULT_REVISION,
                                local_files_only=args.offline, cache_dir=args.cache_dir)
    return Catalog.open(chosen, source=args.source, offline=args.offline, cache_dir=args.cache_dir)


def _filters_given(args: argparse.Namespace) -> bool:
    keys = ("platform", "organism", "tissue", "disease", "gse", "split", "stain", "min_obs", "max_obs", "text",
            "gene")
    return (any(getattr(args, k, None) for k in keys) or bool(getattr(args, "where", None))
            or getattr(args, "has_image", None) is not None or bool(_query(args)))


def _query(args: argparse.Namespace) -> str | None:
    value = getattr(args, "query", None)
    if isinstance(value, list):
        value = " ".join(value)
    return value or None


def apply_filters(catalog: Catalog, args: argparse.Namespace) -> Catalog:
    filters: dict[str, Any] = {}
    for option, name in (("platform", "platform"), ("organism", "organism"), ("gse", "gse"), ("split", "split"),
                         ("stain", "image_stain")):
        values = getattr(args, option, None)
        if values:
            filters[name] = values if len(values) > 1 else values[0]
    if getattr(args, "has_image", None) is not None:
        filters["has_image"] = args.has_image
    where = list(getattr(args, "where", []) or [])
    for option in ("tissue", "disease"):
        if getattr(args, option, None):
            where.append(f"{option}~" + "|".join(getattr(args, option)))
    return catalog.search(_query(args), where=where, text=getattr(args, "text", None),
                          min_obs=getattr(args, "min_obs", None), max_obs=getattr(args, "max_obs", None),
                          match="any" if getattr(args, "any", False) else "all", sort=getattr(args, "sort", None),
                          genes=getattr(args, "gene", None), gene_match="any" if getattr(args, "gene_any", False)
                          else "all", feature_index=getattr(args, "feature_index", None), **filters)


def _read_ids(source: str) -> tuple[list[str], dict[str, Any] | None]:
    """Accessions from a selection/search JSON, JSONL, a CSV/TSV with a gsm column, or free text.

    Returns (ids, record) where record carries the commit the IDs were selected at, if known.
    """
    import csv

    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            record = json.loads(text)
        except json.JSONDecodeError:  # JSON Lines, one record per line
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            return [r["gsm"] for r in rows if isinstance(r, dict) and r.get("gsm")], None
        if not isinstance(record.get("samples"), list):
            raise ValueError(f"{source} is JSON but has no 'samples' list (expected a selection or search result)")
        if record.get("truncated"):
            _note(f"warning: {source} lists only the {len(record['samples'])} rows shown of {record.get('total')}; "
                  "save complete selections with 'search --output FILE.json'")
        commit = record.get("revision") or record.get("commit")
        return [row["gsm"] for row in record["samples"]], ({**record, "revision": commit} if commit else None)
    first = stripped.splitlines()[0] if stripped else ""
    for delimiter in (",", "\t"):
        header = [h.strip() for h in first.split(delimiter)]
        if "gsm" in header and len(header) > 1:
            reader = csv.DictReader(io.StringIO(text.lstrip()), delimiter=delimiter)
            return [row["gsm"] for row in reader if row.get("gsm")], None
    gsms = re.findall(r"\bGSM[0-9]+\b", text)
    series = re.findall(r"\bGSE[0-9]+\b", text)
    if gsms and series:
        _note(f"note: {source} lists GSM and GSE accessions; using the {len(gsms)} GSMs only")
    ids = gsms or series
    if not ids:
        raise NotFoundError(f"No GSM/GSE accessions found in {source}")
    return ids, None


def select_ids(args: argparse.Namespace, *, require: bool) -> Catalog:
    """Resolve positional IDs, --from and filters into one selection at one commit."""
    ids = list(getattr(args, "ids", []) or [])
    record = None
    if getattr(args, "from_file", None):
        more, record = _read_ids(args.from_file)
        ids += more
    release = args.release
    if record and not release:
        release = record.get("revision")
    catalog = open_catalog(args, release=release)
    if record and record.get("revision") != catalog.revision:
        _note(f"warning: {args.from_file} was saved at commit {record['revision'][:7]}; "
              f"using {catalog.release_name} ({catalog.revision[:7]})")
    bad = [i for i in ids if not ID.fullmatch(i)]
    if bad:
        raise NotFoundError(f"Not GSM/GSE accessions: {', '.join(bad[:5])}")
    if ids:
        gsms: list[str] = []
        for identifier in ids:
            if identifier.startswith("GSE"):
                found = [r["gsm"] for r in catalog.rows if identifier in r["gse"].split("+")]
                if not found:
                    raise NotFoundError(f"Series {identifier} has no samples in release {catalog.release_name}")
                gsms += found
            else:
                gsms.append(identifier)
        catalog = catalog.select(gsms)
    if _filters_given(args):
        catalog = apply_filters(catalog, args)
    elif require and not ids:
        raise NotFoundError("Nothing selected", hint="Pass GSM/GSE IDs, --from FILE, or filters such as --query.")
    return catalog


def _envelope(catalog: Catalog) -> dict[str, Any]:
    return {"release": catalog.release_name, "commit": catalog.revision, "catalog_source": catalog.source}


# --------------------------------------------------------------------------- commands
def cmd_search(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 0:
        raise ValueError("--limit must be nonnegative")
    catalog = open_catalog(args)
    result = apply_filters(catalog, args)
    if args.output:
        result.export(args.output)
    machine = args.format in {"ids", "csv", "tsv", "jsonl"}
    limit = len(result) if args.all or (machine and args.limit is None) else (
        20 if args.limit is None else args.limit)
    if machine and limit < len(result):
        _note(f"note: showing {limit} of {len(result)} matches (--limit); use --all for every match")
    fields: Any = (args.fields.split(",") if args.fields and args.fields not in {"all", "summary"}
                   else (args.fields or "summary"))
    json_mode = _json_mode(args)
    if args.studies:
        studies = result.list_studies()
        shown = studies[:limit]
        if json_mode:
            _emit({**_envelope(catalog), "query": _query(args), "filters": (result.report or {}).get("filters"),
                   "total_samples": len(result), "total": len(studies), "shown": len(shown),
                   "truncated": len(shown) < len(studies), "studies": shown, "warnings": result.warnings})
        else:
            _note(f"# {catalog.release_name} ({catalog.revision[:7]}) · {len(studies):,} studies, "
                  f"{len(result):,} samples")
            _print_records(args, shown, ("published_gse", "samples", "with_image", "platforms", "title"))
            for warning in result.warnings:
                _note(f"note: {warning}")
        return 0
    records = result.head(limit).to_records(fields)
    if json_mode:
        payload = {**_envelope(catalog), "query": _query(args), "filters": (result.report or {}).get("filters"),
                   "total": len(result), "shown": len(records), "truncated": len(records) < len(result)}
        if fields == "summary":
            payload.update(result.compact(limit))
            payload["omitted"] = "fields that are empty for a sample are left out"
        else:
            payload["samples"] = records
        payload["warnings"] = result.warnings
        if result.report and result.report.get("terms"):
            payload["term_matches"] = result.report["terms"]
        if args.output:
            payload["saved"] = str(args.output)
        if result.rows:
            saved = str(args.output) if args.output and args.output.suffix == ".json" else "cohort.json"
            payload["next"] = [f"sda-db show {result.rows[0]['gsm']}",
                               *([] if args.output else [f"repeat with --output {saved} to save all {len(result)}"]),
                               f"sda-db download --from {saved} --dry-run"]
        _emit(payload)
    else:
        if args.format in {"csv", "tsv", "jsonl", "ids"}:
            columns = list(records[0].keys()) if records else list(SUMMARY_FIELDS)
        else:
            columns = (list(TABLE_FIELDS) + (["score"] if result.scores is not None else [])
                       if fields == "summary" or not records else list(records[0].keys()))
            _note(f"# {catalog.release_name} ({catalog.revision[:7]}) · {len(result):,} of {len(catalog):,} samples, "
                  f"{len({r['published_gse'] for r in result.rows}):,} studies"
                  + (f" · showing {len(records)}" if len(records) < len(result) else ""))
        _print_records(args, records, columns)
        for warning in result.warnings:
            _note(f"note: {warning}")
        if args.output:
            _note(f"saved {len(result):,} samples to {args.output}")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    catalog = open_catalog(args)
    fields = catalog.fields()
    if _json_mode(args):
        _emit({**_envelope(catalog), "fields": fields,
               "operators": "FIELD=V  FIELD!=V  FIELD~TEXT  FIELD!~TEXT  FIELD>=N  FIELD<=N  (| = OR; empty = missing)"})
    else:
        for entry in fields:
            entry["top"] = ", ".join(map(str, entry.get("top_values", [])[:3]))
        _print_records(args, fields, ("field", "kind", "distinct", "missing", "description"))
    return 0


def cmd_values(args: argparse.Namespace) -> int:
    catalog = open_catalog(args)
    selection = apply_filters(catalog, args) if _filters_given(args) else catalog
    values = selection.values(args.field, top=None)
    shown = values[: args.top] if args.top else values
    if _json_mode(args):
        _emit({**_envelope(catalog), "field": args.field, "selection_samples": len(selection),
               "distinct": len(values), "shown": len(shown), "values": shown, "warnings": selection.warnings})
    else:
        _note(f"# {args.field}: {len(values):,} distinct values in {len(selection):,} samples")
        _print_records(args, shown, ("value", "samples", "studies", "observations"))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    catalog = open_catalog(args)
    selection = apply_filters(catalog, args) if _filters_given(args) else catalog
    result = selection.summary(by=[b.strip() for b in args.by.split(",")] if args.by else None)
    result["warnings"] = selection.warnings
    if _json_mode(args):
        _emit(result)
    else:
        totals = result["totals"]
        say = print if (args.format or "table") == "table" else _note
        say(f"{totals['samples']:,} samples · {totals['studies']:,} studies · {totals['observations']:,} "
              f"observations · {totals['with_image']:,} with image · {totals['platforms']} platforms · "
              f"{totals['organisms']} organisms  [{result['release']} {result['commit'][:7]}]")
        if args.by:
            if say is print:
                print()
            _print_records(args, result["groups"], result["by"] + ["samples", "studies", "observations",
                                                                  "with_image"])
    return 0


def _print_card(value: dict[str, Any], indent: int = 0) -> None:
    for key, item in value.items():
        if isinstance(item, dict):
            if not any(v not in (None, "", [], {}) for v in item.values()):
                continue
            print(" " * indent + f"{key}:")
            _print_card(item, indent + 2)
        elif item not in (None, "", [], {}):
            text = (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) or (
                isinstance(item, list) and any(isinstance(v, (list, dict)) for v in item)) else _cell(item))
            print(" " * indent + f"{key}: {text}")


def cmd_show(args: argparse.Namespace) -> int:
    catalog = open_catalog(args)
    cards = []
    for identifier in args.ids:
        if identifier.startswith("GSE"):
            cards.append({"type": "study", **catalog.study(identifier)})
        else:
            cards.append({"type": "sample", **catalog.card(identifier)})
    if _json_mode(args):
        _emit({**_envelope(catalog), "items": cards})
    else:
        for card in cards:
            if card["type"] == "study":
                detail = card.pop("samples_detail")
                _print_card(card)
                print()
                _print_records(args, detail, ("gsm", "platform", "tissue", "n_obs", "has_image", "split"))
            else:
                _print_card(card)
            print()
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    from .batch import download, human_bytes, parse_size, plan_download

    selection = select_ids(args, require=True)
    if not len(selection):
        raise NotFoundError("The selection is empty", hint="Check the filters with 'sda-db search' first.")
    json_mode = _json_mode(args)
    limit = parse_size(args.max_size)
    guard = limit if limit is not None else (None if args.yes else DEFAULT_GUARD_BYTES)
    plan = plan_download(selection, destination=args.destination, include_image=args.include_image) \
        if (args.dry_run or guard is not None) else None
    if args.dry_run:
        payload = plan.to_dict(per_sample=json_mode)
        if json_mode:
            _emit(payload)
        else:
            print(f"{payload['samples']:,} samples from {payload['release']} ({payload['commit'][:7]}) → "
                  f"{payload['destination']}")
            print(f"total {payload['total']}, still to fetch {payload['to_fetch']}"
                  + (f", {payload['already_local']} already local" if payload["already_local"] else "")
                  + (f", free {human_bytes(payload['free_bytes'])}" if payload["free_bytes"] else ""))
            largest = sorted(plan.samples, key=lambda s: -(s.bytes or 0))[:5]
            print("largest: " + ", ".join(f"{s.gsm} {human_bytes(s.bytes)}" for s in largest))
            for note in payload["notes"]:
                _note(f"note: {note}")
        return 0
    if guard is not None:  # fail closed: unknown sizes never pass a size limit
        if plan.access_denied:
            raise AccessError("Downloading needs approved dataset access", hint=ACCESS_HINT)
        if plan.fetch_bytes is None:
            raise SizeLimitError("Some file sizes are unknown, so the size limit cannot be checked",
                                 hint="Inspect with --dry-run; pass --yes to download without a size check.")
        if plan.fetch_bytes > guard:
            raise SizeLimitError(f"This would fetch {human_bytes(plan.fetch_bytes)} for {len(selection)} samples, "
                                 f"above the limit of {human_bytes(guard)}",
                                 hint="Narrow the selection or omit images; raise --max-size, or pass --yes "
                                      "(only without --max-size) to skip the 20 GB safety limit.")
    try:
        report = download(selection, args.destination, include_image=args.include_image, workers=args.workers,
                          storage=args.storage, on_error="continue" if args.keep_going else "raise",
                          progress=None if not json_mode else False)
    except Exception as exc:
        partial = getattr(exc, "report", None)
        if args.report and partial is not None:
            partial.save(args.report)
        raise
    if args.report:
        report.save(args.report)
    if json_mode:
        _emit(report.to_dict())
    else:
        for item in report.completed:
            print(item["directory"])
        _note(report.to_dict()["summary"])
        for item in report.failed:
            _note(f"failed {item['gsm']}: {item['error']['message']}")
    return 0 if report.ok else 1


def _sample_dirs(paths: Iterable[Path]) -> list[Path]:
    result = []
    for path in paths:
        if (path / "sample.json").is_file():
            result.append(path)
        elif (path / "samples").is_dir():
            result += sorted(p.parent for p in (path / "samples").glob("*/*/sample.json"))
        else:
            raise NotFoundError(f"{path} is neither a sample directory nor a downloads/<commit> folder")
    return result


def cmd_verify(args: argparse.Namespace) -> int:
    results, failures = [], 0
    for directory in _sample_dirs(args.directory):
        try:
            results.append({"directory": str(directory), "ok": True,
                            **verify_sample(directory, require_image=args.require_image)})
        except (ValueError, OSError) as exc:
            failures += 1
            error = translate(exc) or exc
            results.append({"directory": str(directory), "ok": False, "error": str(error)})
    if _json_mode(args):
        _emit(results[0] if len(results) == 1 and len(args.directory) == 1 and results[0]["ok"] else
              {"checked": len(results), "failed": failures, "results": results})
    else:
        for item in results:
            print(("ok    " if item["ok"] else "FAIL  ") + item["directory"]
                  + ("" if item["ok"] else f"  {item['error']}"))
        _note(f"{len(results) - failures} of {len(results)} samples verified")
    return 0 if not failures else 5


def cmd_inspect(args: argparse.Namespace) -> int:
    from .io import describe_sample

    target = Path(args.target)
    if not target.exists() and re.fullmatch(r"GSM[0-9]+", args.target):
        catalog = open_catalog(args)
        row = catalog.get(args.target)
        target = args.destination / catalog.revision / row["sample_dir"]
        if not (target / "sample.json").is_file():
            raise NotFoundError(f"{args.target} is not downloaded under {args.destination}",
                                hint=f"Run 'sda-db download {args.target}' first.")
    info = describe_sample(target, verify=args.verify)
    if _json_mode(args):
        _emit(info)
    else:
        _print_card(info)
    return 0


def cmd_features(args: argparse.Namespace) -> int:
    catalog = open_catalog(args)
    info = catalog.features(args.gsm, feature_index=args.feature_index)
    rows = info.pop("rows")
    note = None
    if rows is None:
        note = (f"Whole-transcriptome panel: counts only. Test genes with 'sda-db search --gene G --where "
                f"gsm={args.gsm}', or read adata.var after 'sda-db download {args.gsm}'.")
        rows = []
    if args.contains:
        needle = args.contains.casefold()
        rows = [r for r in rows if needle in r["feature_name"].casefold() or needle in r["source_feature_id"].casefold()]
    shown = rows[: args.limit] if args.limit else rows
    if _json_mode(args):
        _emit({**_envelope(catalog), "gsm": args.gsm, **info, "matching": len(rows), "shown": len(shown),
               "rows": shown, **({"note": note} if note else {})})
    else:
        kinds = ", ".join(f"{kind or 'unspecified'} {count:,}" for kind, count in info["feature_types"].items())
        _note(f"# {args.gsm}: {info['features']:,} features ({kinds})"
              + (f" · showing {len(shown)} of {len(rows):,}" if rows and len(shown) < len(rows) else ""))
        if note:
            _note(f"note: {note}")
        _print_records(args, shown, ("feature_name", "source_feature_id", "feature_type"))
    return 0


def cmd_cite(args: argparse.Namespace) -> int:
    selection = select_ids(args, require=False)
    text = selection.cite(format=args.style)
    if args.output:
        args.output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        studies = len({g for r in selection.rows for g in r["gse"].split("+")})
        _note(f"wrote citations for {len(selection):,} samples from {studies:,} GEO series to {args.output}")
    else:
        print(text)
    return 0


def cmd_releases(args: argparse.Namespace) -> int:
    from .releases import known_releases, registry, resolve_release

    entries = [{**r.to_dict(), "default": r.name == registry()["default"]} for r in known_releases()]
    latest = registry()["latest"]
    item = {"name": "latest", "aliases": latest.get("aliases"), "ref": latest["ref"], "rolling": True,
            "description": latest["description"]}
    if args.resolve_latest:
        resolved = resolve_release("latest")
        item["revision"] = resolved.revision
    entries.append(item)
    if _json_mode(args):
        _emit({"releases": entries})
    else:
        for entry in entries:
            entry["commit"] = (entry.get("revision") or "")[:7] or entry.get("ref")
            entry["mark"] = "*" if entry.get("default") else ""
        _print_records(args, entries, ("mark", "name", "commit", "sample_count", "description"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run

    result = run(release=args.release, destination=args.destination, network=not args.offline)
    if _json_mode(args):
        _emit(result)
    else:
        for check in result["checks"]:
            mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[check["status"]]
            print(f"{mark}  {check['check']:<16} {check['detail']}")
            if check.get("fix"):
                print(f"      fix: {check['fix']}")
    return 0 if result["status"] != "fail" else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    from .batch import parse_size
    from .mcp_server import serve

    serve(release=args.release, download_dir=args.download_dir,
          max_download_bytes=parse_size(args.max_download_size), allow_downloads=args.allow_downloads)
    return 0


COMMANDS = {"search": cmd_search, "fields": cmd_fields, "values": cmd_values, "summary": cmd_summary,
            "show": cmd_show, "download": cmd_download, "verify": cmd_verify, "inspect": cmd_inspect,
            "cite": cmd_cite, "releases": cmd_releases, "doctor": cmd_doctor, "mcp": cmd_mcp,
            "features": cmd_features}


def _configure_logging(verbose: bool, quiet_hub: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    if not verbose:
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.ERROR)
        os.environ.setdefault("HF_HUB_DISABLE_EXPERIMENTAL_WARNING", "1")
        from huggingface_hub.utils import logging as hub_logging

        hub_logging.set_verbosity_error()  # e.g. server notices about anonymous requests
        if quiet_hub:
            try:
                from huggingface_hub.utils import disable_progress_bars

                disable_progress_bars()
            except ImportError:  # pragma: no cover
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; exit codes: 0 ok, 1 error, 2 usage, 3 not found, 4 access, 5 integrity, 6 network, 7 size limit, 8 disk full."""
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras:
        # Exclusions such as -mouse or -xeno* look like options to argparse: keep them as keywords.
        words = [e for e in extras if e.startswith("-") and not e.startswith("--") and len(e) > 2]
        if args.command == "search" and len(words) == len(extras):
            args.query = list(args.query) + words
        elif "query" in vars(args) and isinstance(args.query, str) and len(words) == len(extras):
            args.query = " ".join([args.query, *words])
        else:
            parser.error(f"unrecognized arguments: {' '.join(extras)}")
    json_mode = _json_mode(args) if args.command not in {"cite", "mcp"} else False
    _configure_logging(args.verbose, quiet_hub=json_mode or args.command == "mcp" or not sys.stderr.isatty())
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        _note("interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - every failure becomes a coded message
        error = translate(exc)
        if error is None:
            if not isinstance(exc, (ValueError, FileNotFoundError, ImportError, PermissionError, OSError)):
                LOGGER.debug("unexpected error", exc_info=True)
                error = SdaDbError(f"{type(exc).__name__}: {exc}" if args.verbose else type(exc).__name__,
                                   hint="Re-run with -v for details; report persistent failures as an issue.")
            else:
                error = SdaDbError(str(exc))
                if isinstance(exc, ImportError):
                    error.code, error.hint = "missing_dependency", "pip install 'spatial-data-agent-db[analysis]'"
        if json_mode:
            print(json.dumps({"error": error.to_dict()}, ensure_ascii=False), file=sys.stderr)
        else:
            _note(f"error: {error}")
            if error.hint:
                _note(f"hint: {error.hint}")
        return error.exit_code
