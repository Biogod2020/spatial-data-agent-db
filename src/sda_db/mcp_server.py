"""Model Context Protocol server (stdio, JSON-RPC 2.0) exposing the catalog to AI agents.

Dependency-free: `sda-db mcp` works with the core install. Tools are read-only except
`download_samples`, which plans by default, enforces a size limit and writes only to
a server-controlled directory.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Callable, TextIO

from .catalog import Catalog
from .errors import NotFoundError, SdaDbError, SizeLimitError, UnknownValueError, translate

LOGGER = logging.getLogger(__name__)
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_MAX_DOWNLOAD = 20 * 10**9
MAX_ROWS = 200
INSTRUCTIONS = """SpatialDataAgent-DB is a versioned atlas of public spatial transcriptomics samples (mostly from GEO:
Visium, Visium HD, Xenium, CosMx, MERFISH/MERSCOPE, Slide-seq, Stereo-seq, legacy ST and more). Each sample has
verified expression (AnnData .h5ad), spatial coordinates, release metadata (sample.json) and, for many, a
registered pyramidal tissue image.

How to use these tools:
1. search_samples with keywords and filters. Use list_fields and field_values to learn exact vocabularies
   instead of guessing; organism and platform accept common names ('human', 'visium hd').
   Results are ranked; 'total' tells you how many matched beyond the rows returned.
   The default release adds curated metadata v2: organ, disease (MONDO ID or name), disease_status,
   sample_kind, life_stage, sex, assay_type, assay_name, preservation, plus min_confidence and exclude_flags
   to drop uncertain annotations. Release 'paper' is the paper release with its original metadata only.
2. get_sample / get_study for details (biology, assay, evidence, QC, image registration, publications).
3. plan_download before download_samples(confirm=true). Images are large and opt-in.
4. inspect_sample describes a downloaded sample; cite returns BibTeX for the database and original studies.

Report the release name and commit with any selection you give the user. Do not assume X holds raw counts
(check expression_state) or that coordinates are micrometres (check coordinate_units and um_basis). Observations
can be spots, bins, cells or beads. For machine learning keep the release split and keep each study together.
Searching needs no login; downloading needs approved Hugging Face access (check_access explains how)."""

_FILTERS: dict[str, Any] = {
    "query": {"type": "string", "description": "Keywords over tissue, disease, organism, platform, GSM/GSE IDs and "
              "study title/design/summary. All terms must match; supports \"quoted phrase\", -exclude and prefix*."},
    "platform": {"type": "string", "description": "Exact platform, e.g. Visium, 'Visium HD', Xenium, CosMx, "
                 "'MERFISH/MERSCOPE', Slide-seq, Stereo-seq, 'ST (legacy)'. Common names work. 'A|B' = OR."},
    "organism": {"type": "string", "description": "Organism: scientific or common name ('human', 'mouse'). 'A|B' = OR."},
    "tissue": {"type": "string", "description": "Case-insensitive tissue substring as written in GEO, e.g. 'brain'."},
    "disease": {"type": "string", "description": "Metadata v2 (default release): MONDO ID ('MONDO:0004975') or "
                "disease name, matched exactly or as a case-insensitive substring of MONDO names (no ancestor "
                "expansion). Paper release: substring of the disease text reported in GEO. 'A|B' = OR."},
    "organ": {"type": "string", "description": "Metadata v2 organ from a fixed list of 43 (lung, brain, colon, "
              "liver, ...). 'A|B' = OR."},
    "disease_status": {"type": "string", "description": "Metadata v2: normal, disease, perturbation or 'not stated'."},
    "sample_kind": {"type": "string", "description": "Metadata v2: 'primary tissue', 'tumour tissue', metastasis, "
                    "organoid, xenograft, 'cell culture', 'whole embryo/organism', 'engineered tissue'."},
    "life_stage": {"type": "string", "description": "Metadata v2: embryonic, fetal, 'neonatal or infant', juvenile, "
                   "adult, aged or 'not stated'."},
    "sex": {"type": "string", "description": "Metadata v2: male, female, mixed or unknown (paper release: as "
            "reported)."},
    "assay_type": {"type": "string", "description": "Metadata v2: imaging-based or sequencing-based."},
    "assay_name": {"type": "string", "description": "Metadata v2 specific assay, e.g. 'Visium CytAssist', "
                   "'Slide-seqV2', 'Open-ST'."},
    "preservation": {"type": "string", "description": "Metadata v2: FFPE, 'fresh frozen' or 'fixed frozen'."},
    "min_confidence": {"type": "number", "description": "Metadata v2: keep samples whose lowest annotation "
                       "confidence is at least this (0-1); 0.6 drops all low-confidence calls."},
    "exclude_flags": {"type": "string", "description": "Metadata v2: 'any' drops samples with any metadata flag; "
                      "or comma-separated flags, e.g. 'normal_but_disease_text,feature_species_differs'."},
    "gse": {"type": "string", "description": "GEO series accession(s); 'A|B' = OR."},
    "gsms": {"type": "string", "description": "Restrict to these GSM accessions (comma or space separated)."},
    "split": {"type": "string", "description": "Release split: train, val or test ('train|val' = OR)."},
    "image_stain": {"type": "string", "description": "Exact image stain, e.g. 'H&E' or 'fluorescence: DAPI'."},
    "has_image": {"type": "boolean", "description": "Only samples with (true) or without (false) a released image."},
    "min_obs": {"type": "integer", "description": "Minimum observations (spots/cells/bins/beads)."},
    "max_obs": {"type": "integer", "description": "Maximum observations."},
    "where": {"type": "array", "items": {"type": "string"}, "description": "Extra conditions: FIELD=V, FIELD!=V, "
              "FIELD~TEXT, FIELD>=N, FIELD<=N; '|' = OR; empty value = missing. Example: ['n_vars>=5000']."},
    "genes": {"type": "string", "description": "Only samples whose measured features include these genes or "
              "feature IDs (comma or space separated, any case). Measured is not detected; whole-transcriptome "
              "samples measure almost all genes, so add where=['n_vars<=10000'] to find targeted panels."},
    "gene_match": {"type": "string", "enum": ["all", "any"], "description": "With several genes: all (default) or any."},
    "match": {"type": "string", "enum": ["all", "any"], "description": "Keyword logic (default all)."},
    "sort": {"type": "string", "description": "Sort by field; prefix '-' for descending, e.g. '-n_obs'."},
    "release": {"type": "string", "description": "Release name, 'latest' or commit (default: v1.1.1-20261106 = paper "
                "data + metadata v2; 'paper' = the paper release as published)."},
}
_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


class Server:
    def __init__(self, *, release: str | None = None, download_dir: str | Path | None = None,
                 max_download_bytes: int | None = None, allow_downloads: bool = True) -> None:
        self.default_release = release
        self.download_dir = Path(download_dir or os.environ.get("SDA_DB_DOWNLOAD_DIR")
                                 or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sda-db"
                                 / "downloads").expanduser()
        if max_download_bytes is None:
            from .batch import parse_size

            max_download_bytes = parse_size(os.environ.get("SDA_DB_MAX_DOWNLOAD_BYTES") or DEFAULT_MAX_DOWNLOAD)
        self.max_download_bytes = int(max_download_bytes)
        self.allow_downloads = allow_downloads
        self._catalogs: dict[str | None, Catalog] = {}
        self.tools: dict[str, tuple[dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {}
        self._register()

    # ------------------------------------------------------------------ helpers
    def catalog(self, release: str | None = None) -> Catalog:
        key = release or self.default_release
        if key not in self._catalogs:
            self._catalogs[key] = Catalog.open(key)
        return self._catalogs[key]

    def _select(self, args: dict[str, Any]) -> Catalog:
        catalog = self.catalog(args.get("release"))
        selection = catalog
        if args.get("gsms"):
            ids = [t for t in str(args["gsms"]).replace(",", " ").split() if t]
            selection = selection.select(ids)
        filters: dict[str, Any] = {}
        curated = bool(catalog.metadata_v2)
        for name in ("platform", "organism", "gse", "split", "image_stain", "organ", "disease_status", "sample_kind",
                     "life_stage", "sex", "assay_type", "assay_name", "preservation",
                     *(("disease",) if curated else ())):
            if args.get(name):
                values = [v.strip() for v in str(args[name]).split("|") if v.strip()]
                filters[name] = values if len(values) > 1 else values[0]
        if args.get("has_image") is not None:
            filters["has_image"] = args["has_image"] is True
        where = list(args.get("where") or [])
        for name in ("tissue",) if curated else ("tissue", "disease"):
            if args.get(name):
                where.append(f"{name}~{args[name]}")
        if filters or where or args.get("query") or args.get("min_obs") is not None or args.get("max_obs") is not None \
                or args.get("sort") or args.get("genes") or args.get("min_confidence") is not None \
                or args.get("exclude_flags"):
            selection = selection.search(args.get("query"), where=where, min_obs=args.get("min_obs"),
                                         max_obs=args.get("max_obs"), match=args.get("match", "all"),
                                         sort=args.get("sort"), genes=args.get("genes") or None,
                                         gene_match=args.get("gene_match", "all"),
                                         min_confidence=args.get("min_confidence"),
                                         exclude_flags=args.get("exclude_flags") or None, **filters)
        return selection

    @staticmethod
    def _envelope(catalog: Catalog) -> dict[str, Any]:
        envelope = {"release": catalog.release_name, "commit": catalog.revision}
        if catalog.metadata_v2:
            envelope["metadata"] = catalog.metadata_v2.get("tag")
        return envelope

    def _register(self) -> None:
        def tool(name: str, title: str, description: str, schema: dict[str, Any],
                 annotations: dict[str, Any] | None = None):
            def wrap(function: Callable[[dict[str, Any]], dict[str, Any]]):
                self.tools[name] = ({"name": name, "title": title, "description": description,
                                     "inputSchema": schema, "annotations": {"title": title, **(annotations or _READ)}},
                                    function)
                return function
            return wrap

        @tool("search_samples", "Search samples",
              "Find samples by ranked keywords and structured filters (no download). Returns compact rows; "
              "'total' is the full match count. Set group_by_study=true for one row per study.",
              _schema({**_FILTERS,
                       "limit": {"type": "integer", "description": f"Rows to return (default 20, max {MAX_ROWS})."},
                       "fields": {"type": "string", "description": "'summary' (default), 'all' or comma-separated "
                                  "field names."},
                       "group_by_study": {"type": "boolean", "description": "Return studies instead of samples."}}))
        def search_samples(args: dict[str, Any]) -> dict[str, Any]:
            selection = self._select(args)
            limit = max(0, min(int(args.get("limit", 20)), MAX_ROWS))
            result = {**self._envelope(selection), "filters": (selection.report or {}).get("filters"),
                      "total": len(selection), "warnings": selection.warnings}
            if args.get("group_by_study"):
                studies = selection.list_studies()
                result.update(total_studies=len(studies), shown=min(limit, len(studies)),
                              studies=studies[:limit])
            else:
                fields = args.get("fields") or "summary"
                if fields == "summary":
                    compact = selection.compact(limit)
                    result.update(shown=len(compact["samples"]), **compact,
                                  omitted="fields that are empty for a sample are left out")
                else:
                    chosen = fields if fields == "all" else [f.strip() for f in fields.split(",")]
                    rows = selection.head(limit).to_records(chosen)
                    result.update(shown=len(rows), samples=rows)
                if selection.report and selection.report.get("terms"):
                    result["term_matches"] = selection.report["terms"]
            result["truncated"] = result["shown"] < (result.get("total_studies") or result["total"])
            if result["truncated"]:
                result["note"] = "More matches exist: narrow the filters, raise limit, or use group_by_study."
            return result

        @tool("list_fields", "List catalog fields",
              "Every searchable field with its meaning, kind, distinct count and top values, plus filter syntax.",
              _schema({"release": _FILTERS["release"]}))
        def list_fields(args: dict[str, Any]) -> dict[str, Any]:
            catalog = self.catalog(args.get("release"))
            return {**self._envelope(catalog), "fields": catalog.fields(),
                    "operators": "FIELD=V FIELD!=V FIELD~TEXT FIELD!~TEXT FIELD>=N FIELD<=N; '|' = OR; empty = missing"}

        @tool("field_values", "Field values with counts",
              "Distinct values of one field (e.g. platform, tissue, disease, image_stain, organism) with sample, "
              "study and observation counts, optionally within a filtered selection.",
              _schema({"field": {"type": "string", "description": "Catalog field name."},
                       "top": {"type": "integer", "description": "Values to return (default 30, max 500)."},
                       **_FILTERS}, ["field"]))
        def field_values(args: dict[str, Any]) -> dict[str, Any]:
            selection = self._select(args)
            values = selection.values(args["field"])
            top = max(1, min(int(args.get("top", 30)), 500))
            return {**self._envelope(selection), "field": args["field"], "selection_samples": len(selection),
                    "distinct": len(values), "values": values[:top], "truncated": len(values) > top}

        @tool("summarize", "Summarize a selection",
              "Totals (samples, studies, observations, images, platforms, organisms) for a selection, optionally "
              "cross-tabulated by comma-separated fields such as 'platform,organism'.",
              _schema({"by": {"type": "string", "description": "Comma-separated fields."}, **_FILTERS}))
        def summarize(args: dict[str, Any]) -> dict[str, Any]:
            selection = self._select(args)
            result = selection.summary(by=[b.strip() for b in args["by"].split(",")] if args.get("by") else None)
            if len(result.get("groups", [])) > MAX_ROWS:
                result["groups"] = result["groups"][:MAX_ROWS]
                result["truncated"] = True
            return result

        @tool("get_sample", "Get sample details",
              "Everything the catalog records for one GSM: identity, study, biology, assay, expression evidence, "
              "coordinates, image, subcellular layers, QC/review, split, provenance and file paths. "
              "include_sample_json=true also fetches its sample.json (needs dataset access).",
              _schema({"gsm": {"type": "string"}, "include_sample_json": {"type": "boolean"},
                       "release": _FILTERS["release"]}, ["gsm"]))
        def get_sample(args: dict[str, Any]) -> dict[str, Any]:
            catalog = self.catalog(args.get("release"))
            card = catalog.card(args["gsm"])
            if args.get("include_sample_json"):
                from huggingface_hub import hf_hub_download

                from .errors import hub_errors

                try:
                    with hub_errors():
                        path = hf_hub_download(catalog.repo_id, f"{card['identity']['sample_dir']}/sample.json",
                                               repo_type="dataset", revision=catalog.revision)
                    value = json.loads(Path(path).read_text(encoding="utf-8"))
                    value.pop("provenance", None)
                    card["sample_json"] = value
                except SdaDbError as exc:
                    card["sample_json_error"] = exc.to_dict()
            return card

        @tool("list_features", "List measured features",
              "Genes/features measured in one sample (name, source ID, type), from the release's feature "
              "index; no download. Use 'contains' to check for specific genes.",
              _schema({"gsm": {"type": "string"}, "contains": {"type": "string"},
                       "limit": {"type": "integer", "description": "Features to return (default 100, max 2000)."},
                       "release": _FILTERS["release"]}, ["gsm"]))
        def list_features(args: dict[str, Any]) -> dict[str, Any]:
            catalog = self.catalog(args.get("release"))
            info = catalog.features(args["gsm"])
            rows = info.pop("rows")
            if rows is None:
                return {**self._envelope(catalog), "gsm": args["gsm"], **info,
                        "note": "Whole-transcriptome panel: counts only. Test genes with search_samples(genes=..., "
                                f"gsms='{args['gsm']}')."}
            if args.get("contains"):
                needle = args["contains"].casefold()
                rows = [r for r in rows if needle in r["feature_name"].casefold()
                        or needle in r["source_feature_id"].casefold()]
            limit = max(1, min(int(args.get("limit", 100)), 2000))
            return {**self._envelope(catalog), "gsm": args["gsm"], **info, "matching": len(rows),
                    "shown": min(limit, len(rows)), "rows": rows[:limit], "truncated": len(rows) > limit}

        @tool("get_study", "Get study details",
              "Title, summary, design, PubMed IDs, aggregate counts and compact sample rows for one GEO series.",
              _schema({"gse": {"type": "string"}, "release": _FILTERS["release"]}, ["gse"]))
        def get_study(args: dict[str, Any]) -> dict[str, Any]:
            catalog = self.catalog(args.get("release"))
            study = catalog.study(args["gse"])
            if len(study["samples_detail"]) > MAX_ROWS:
                study["samples_detail"] = study["samples_detail"][:MAX_ROWS]
                study["samples_truncated"] = True
            return {**self._envelope(catalog), **study}

        selection_schema = {**_FILTERS, "include_image": {"type": "boolean",
                                                          "description": "Also fetch image.tif (large) when released."}}

        @tool("plan_download", "Plan a download",
              "Sizes per sample and in total, what is already downloaded, and free disk space, before any transfer. "
              "Select with gsms, gse and/or filters.",
              _schema(selection_schema))
        def plan_download(args: dict[str, Any]) -> dict[str, Any]:
            selection = self._require_selection(args)
            plan = selection.plan_download(destination=self.download_dir, include_image=bool(args.get("include_image")))
            result = plan.to_dict()
            result["per_sample"] = result["per_sample"][:100]
            result["limit_bytes"] = self.max_download_bytes
            result["within_limit"] = plan.fetch_bytes is not None and plan.fetch_bytes <= self.max_download_bytes
            return result

        if self.allow_downloads:
            @tool("download_samples", "Download samples",
                  "Download and verify the selected samples into the server's download directory. Without "
                  "confirm=true this only returns the plan. Refuses selections above the server size limit.",
                  _schema({**selection_schema, "confirm": {"type": "boolean",
                                                           "description": "Set true to transfer after reviewing the plan."}}),
                  {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
            def download_samples(args: dict[str, Any]) -> dict[str, Any]:
                selection = self._require_selection(args)
                include_image = bool(args.get("include_image"))
                plan = selection.plan_download(destination=self.download_dir, include_image=include_image)
                if args.get("confirm") is not True:
                    return {"status": "planned", **plan.to_dict(per_sample=False),
                            "limit_bytes": self.max_download_bytes,
                            "next": "Call download_samples again with confirm=true to transfer."}
                if plan.fetch_bytes is None:
                    raise SizeLimitError("Sizes are unknown, so the size limit cannot be enforced",
                                         hint="Call check_access; downloads need approved dataset access.")
                if plan.fetch_bytes > self.max_download_bytes:
                    raise SizeLimitError(f"Needs {plan.to_dict()['to_fetch']}, above the server limit",
                                         hint="Narrow the selection or omit images; the limit is set by the user.")
                report = selection.download(self.download_dir, include_image=include_image, workers=4,
                                            on_error="continue", progress=False)
                return {"status": "done" if report.ok else "partial", **report.to_dict()}

        @tool("inspect_sample", "Inspect a downloaded sample",
              "Shape, feature types, obs/var columns, coordinate units and range, QC and image levels of a sample "
              "already in the server's download directory (needs the analysis extra).",
              _schema({"gsm": {"type": "string"}, "release": _FILTERS["release"]}, ["gsm"]))
        def inspect_sample(args: dict[str, Any]) -> dict[str, Any]:
            from .io import describe_sample

            catalog = self.catalog(args.get("release"))
            row = catalog.get(args["gsm"])
            directory = self.download_dir / catalog.revision / row["sample_dir"]
            if not (directory / "sample.json").is_file():
                raise NotFoundError(f"{args['gsm']} is not downloaded yet",
                                    hint="Use download_samples with confirm=true first.")
            return describe_sample(directory)

        @tool("cite", "Cite a selection",
              "BibTeX (or text/json) for the database paper, the release and every original GEO series in the "
              "selection.",
              _schema({**_FILTERS, "style": {"type": "string", "enum": ["bibtex", "text", "json"]}}))
        def cite(args: dict[str, Any]) -> dict[str, Any]:
            selection = self._select(args)
            series = {g for r in selection.rows for g in r["gse"].split("+")}
            if len(series) > 300:
                raise SizeLimitError(f"The selection spans {len(series)} series",
                                     hint="Narrow it, or run 'sda-db cite ... -o refs.bib' locally for everything.")
            return {**self._envelope(selection), "series": len(series), "samples": len(selection),
                    "citations": selection.cite(format=args.get("style", "bibtex"))}

        @tool("list_releases", "List releases", "Database releases this client knows; the default is "
              "v1.1.1-20261106 (paper data + curated metadata v2), 'paper' is the paper release as published, and "
              "'latest' is the rolling release with daily reviewed additions.", _schema({}))
        def list_releases(args: dict[str, Any]) -> dict[str, Any]:
            from .releases import known_releases, registry

            return {"default": registry()["default"], "releases": [r.to_dict() for r in known_releases()],
                    "latest": registry()["latest"]}

        @tool("check_access", "Check access",
              "Diagnose Python, network, login and dataset-access status, with a fix for each failure.",
              _schema({"release": _FILTERS["release"]}))
        def check_access(args: dict[str, Any]) -> dict[str, Any]:
            from .doctor import run

            return run(release=args.get("release") or self.default_release, destination=self.download_dir)

    def _require_selection(self, args: dict[str, Any]) -> Catalog:
        keys = set(_FILTERS) - {"release", "match", "sort", "gene_match"}
        if not any(args.get(k) not in (None, "", []) for k in keys):
            raise NotFoundError("Nothing selected", hint="Pass gsms, gse, query or filters.")
        selection = self._select(args)
        if not len(selection):
            raise NotFoundError("The selection is empty", hint="Check it with search_samples first.")
        return selection

    # ------------------------------------------------------------------ protocol
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec, function = self.tools[name]
        allowed = set(spec["inputSchema"]["properties"])
        unknown = set(arguments) - allowed
        try:
            if unknown:
                raise NotFoundError(f"Unknown arguments for {name}: {', '.join(sorted(unknown))}",
                                    hint=f"Allowed: {', '.join(sorted(allowed))}")
            _validate(spec["inputSchema"], arguments)
            with redirect_stdout(sys.stderr):  # keep stdout for protocol messages only
                value = function(arguments)
            return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
                    "structuredContent": json.loads(json.dumps(value, default=str)), "isError": False}
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not protocol errors
            error = translate(exc)
            if error is None:
                LOGGER.debug("tool %s failed", name, exc_info=True)
                error = SdaDbError(f"{type(exc).__name__}: {exc}")
            payload = {"error": error.to_dict()}
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "structuredContent": payload, "isError": True}

    def handle(self, message: Any) -> Any:
        if isinstance(message, list):
            if not message:
                return _error(None, -32600, "Invalid Request: empty batch")
            responses = [r for r in (self.handle(m) for m in message) if r is not None]
            return responses or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
        method, params, identifier = message["method"], message.get("params") or {}, message.get("id")
        if identifier is None:  # notification
            return None
        if not isinstance(params, dict):
            return _error(identifier, -32602, "params must be an object")
        from . import __version__

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _result(identifier, {"protocolVersion": version,
                                        "capabilities": {"tools": {"listChanged": False}},
                                        "serverInfo": {"name": "spatial-data-agent-db", "title": "SpatialDataAgent-DB",
                                                       "version": __version__},
                                        "instructions": INSTRUCTIONS})
        if method == "ping":
            return _result(identifier, {})
        if method == "tools/list":
            return _result(identifier, {"tools": [spec for spec, _ in self.tools.values()]})
        if method == "tools/call":
            name = params.get("name")
            if name not in self.tools:
                return _error(identifier, -32602, f"Unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(identifier, -32602, "arguments must be an object")
            return _result(identifier, self.call_tool(name, arguments))
        if method in {"resources/list", "resources/templates/list", "prompts/list"}:
            key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates",
                   "prompts/list": "prompts"}[method]
            return _result(identifier, {key: []})
        if method == "logging/setLevel":
            return _result(identifier, {})
        return _error(identifier, -32601, f"Method not found: {method}")


_TYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,),
          "object": (dict,)}


def _validate(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Strict type check of tool arguments against the declared input schema."""
    for key in schema.get("required", []):
        if arguments.get(key) in (None, ""):
            raise UnknownValueError(f"Missing required argument {key!r}")
    for key, value in arguments.items():
        if value is None:
            continue
        spec = schema["properties"][key]
        kinds = _TYPES[spec["type"]]
        if not isinstance(value, kinds) or (spec["type"] in {"integer", "number"} and isinstance(value, bool)):
            raise UnknownValueError(f"Argument {key!r} must be a JSON {spec['type']}, not {type(value).__name__}",
                                    hint="Booleans are true/false and numbers are unquoted.")
        if spec["type"] == "array" and not all(isinstance(v, str) for v in value):
            raise UnknownValueError(f"Argument {key!r} must be an array of strings")
        if "enum" in spec and value not in spec["enum"]:
            raise UnknownValueError(f"Argument {key!r} must be one of {spec['enum']}")


def _result(identifier: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def serve(*, release: str | None = None, download_dir: str | Path | None = None,
          max_download_bytes: int | None = None, allow_downloads: bool = True,
          stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Serve newline-delimited JSON-RPC on stdio until stdin closes."""
    server = Server(release=release, download_dir=download_dir, max_download_bytes=max_download_bytes,
                    allow_downloads=allow_downloads)
    wrappers: list[io.TextIOWrapper] = []  # MCP stdio is UTF-8 whatever the console locale (e.g. cp1252)
    if stdin is None:
        stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        wrappers.append(stdin)
    if stdout is None:
        stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n", write_through=True)
        wrappers.append(stdout)
    try:
        _loop(server, stdin, stdout)
    finally:
        for wrapper in wrappers:  # detach, so the process's own stdin/stdout stay open
            try:
                wrapper.flush()
            except ValueError:
                pass
            wrapper.detach()


def _loop(server: Server, source: TextIO, sink: TextIO) -> None:
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: Any = _error(None, -32700, "Parse error")
        else:
            try:
                response = server.handle(message)
            except Exception:  # noqa: BLE001 - never crash the session
                traceback.print_exc(file=sys.stderr)
                response = _error(message.get("id") if isinstance(message, dict) else None, -32603, "Internal error")
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
