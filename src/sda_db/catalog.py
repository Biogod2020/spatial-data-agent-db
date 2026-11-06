"""Catalog discovery without loading expression matrices or scientific dependencies."""

from __future__ import annotations

import csv
import difflib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
from importlib import resources
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator, Sequence

from .errors import IntegrityError, NotFoundError, UnknownFieldError, UnknownValueError, hub_errors
from .query import (BOOLEAN_FIELDS as QUERY_BOOLEANS, CONTROLLED_FIELDS, LIST_SEPARATORS, NUMERIC_FIELDS,
                    V2_CONTROLLED_FIELDS, V2_VALUE_ALIASES, VALUE_ALIASES, Condition, TextIndex, Vocabulary,
                    compile_condition, condition_from_kwarg, disease_term_predicate, members, organism_predicate,
                    parse_where)
from .releases import COMMIT, Release, registry, resolve_release

DEFAULT_REPO = "Biogod/SpatialDataAgent-DB"
DEFAULT_REVISION = next(r["revision"] for r in registry()["releases"] if r["name"] == registry()["default"])
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BOOLEAN_FIELDS = frozenset({"has_image", "has_spatial_um"})
INTEGER_FIELDS = frozenset({"n_obs", "n_vars"})
FLOAT_FIELDS = frozenset({"um_per_unit", "image_um_per_px"})
OPTIONAL_BOOLEAN_FIELDS = frozenset({"frozen_paper_evaluation_eligible"})
FILTER_FIELDS = frozenset({"gsm", "gse", "published_gse", "platform", "organism", "tissue", "split",
                           "source_pipeline", "source_release", "observation_unit", "expression_state",
                           "verification", "coordinate_units", "um_basis", "image_stain", "image_registration",
                           "review_decision", "has_image", "has_spatial_um"})
REQUIRED = frozenset({"gsm", "gse", "published_gse", "sample_dir", "n_obs", "n_vars", "has_image",
                      "expression_sha256", "image_sha256", "expression_state", "coordinate_units", "split"})
METADATA_FIELDS = ("study_title", "disease", "condition", "age", "sex", "donor_id", "publication_pmid",
                   "publication_doi", "counts_ready")
CATALOG_FILE = "catalog/samples.csv"
SAMPLE_METADATA_FILE = "catalog/atlas-sample-metadata.jsonl"
STUDY_METADATA_FILE = "catalog/atlas-study-metadata.jsonl"
# Compact, high-signal columns shown by default in tables, CLI JSON and MCP results.
SUMMARY_FIELDS = ("gsm", "published_gse", "platform", "organism", "tissue", "disease", "n_obs",
                  "observation_unit", "has_image", "image_stain", "expression_state", "coordinate_units",
                  "split", "study_title")
GEO_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={}"
# Curated sample metadata v2 (sda-sample-metadata-2), joined by gsm for releases that record it.
METADATA_V2_SCHEMA = "sda-sample-metadata-2"
METADATA_V2_FIELDS = (
    "organ", "organ_source", "tissue_ontology_id", "tissue_ontology_name", "tissue_raw", "sample_kind",
    "disease_status", "disease_status_source", "disease_ontology_ids", "disease_ontology_names", "disease_raw",
    "condition_raw", "life_stage", "life_stage_source", "life_stage_luna", "age_raw", "sex_raw", "donor_key",
    "donor_id_raw", "organism_taxon_id", "organism_common_name", "organisms", "organism_taxon_ids",
    "co_detected_organisms", "xenograft", "multi_species", "organism_basis", "organism_raw", "feature_species",
    "preservation", "preservation_raw", "platform_basis", "assay_name", "assay_type", "technology_raw",
    "panel_name", "instrument_software", "pubmed_ids", "dois", "annotation_version", "annotation_confidence",
    "annotation_low_confidence", "metadata_flags")
# v2 corrects or harmonizes these v3 columns: the v2 value takes the name, the v3 value stays as <name>_v3.
METADATA_V2_OVERRIDES = ("platform", "organism", "sex", "review_category", "review_notes")
METADATA_V2_BOOLEANS = frozenset({"xenograft", "multi_species"})
# v2 fields that exist only with metadata v2; used for clearer errors on releases without it.
METADATA_V2_ONLY = frozenset(METADATA_V2_FIELDS) | {"annotation_min_confidence"} | {
    f"{name}_v3" for name in METADATA_V2_OVERRIDES}
V2_SUMMARY_FIELDS = ("organ", "sample_kind", "disease_status", "disease_ontology_names", "assay_name")


def sha256_file(path: Path) -> str:
    """Hash files in bounded blocks, including large matrices and images."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=2)
def field_docs(metadata_v2: bool = False) -> dict[str, str]:
    value = json.loads(resources.files("sda_db").joinpath("fields.json").read_text(encoding="utf-8"))
    return {**value["catalog"], **value["metadata"], **(value["metadata_v2"] if metadata_v2 else {})}


def validate_row(row: dict[str, Any]) -> None:
    """Reject inconsistent identities and paths before any download is attempted."""
    if missing := REQUIRED - row.keys():
        raise ValueError(f"Missing catalog fields: {sorted(missing)}")
    if not re.fullmatch(r"GSM[0-9]+", str(row["gsm"])):
        raise ValueError("Invalid GSM identifier")
    if not re.fullmatch(r"GSE[0-9]+", str(row["published_gse"])):
        raise ValueError("Invalid published GSE identifier")
    if not all(re.fullmatch(r"GSE[0-9]+", item) for item in str(row["gse"]).split("+")):
        raise ValueError("Invalid GSE membership")
    if row["published_gse"] not in row["gse"].split("+"):
        raise ValueError("Published GSE is absent from sample membership")
    expected = f"samples/{row['published_gse']}/{row['gsm']}"
    if row["sample_dir"] != expected:
        raise ValueError(f"Invalid sample directory for {row['gsm']}")
    if any(type(row[key]) is not int or row[key] <= 0 for key in INTEGER_FIELDS):
        raise ValueError("Observation and feature counts must be positive integers")
    if any(type(row.get(key)) is not bool for key in BOOLEAN_FIELDS & row.keys()):
        raise ValueError("Image and coordinate presence fields must be booleans")
    if row["split"] not in {"train", "val", "test"}:
        raise ValueError("Invalid split")
    if not SHA256.fullmatch(str(row["expression_sha256"])):
        raise ValueError("Invalid expression SHA256")
    if row["has_image"] and not SHA256.fullmatch(str(row["image_sha256"])):
        raise ValueError("Missing image SHA256")


def _decode_row(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(row)
    for key in BOOLEAN_FIELDS & row.keys():
        value = row[key].casefold()
        if value not in {"true", "false"}:
            raise ValueError(f"Invalid boolean in {key}: {row[key]!r}")
        result[key] = value == "true"
    for key in OPTIONAL_BOOLEAN_FIELDS & row.keys():
        value = row[key].casefold()
        if value not in {"true", "false", ""}:
            raise ValueError(f"Invalid boolean in {key}: {row[key]!r}")
        result[key] = None if value == "" else value == "true"
    for key in INTEGER_FIELDS & row.keys():
        result[key] = int(row[key])
    for key in FLOAT_FIELDS & row.keys():
        result[key] = float(row[key]) if row[key] else None
    validate_row(result)
    return result


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (list, dict)) else str(value)


def read_metadata_v2(path: Path) -> list[dict[str, Any]]:
    """Rows of samples_metadata_v2.csv: lists stay ';'-joined strings, booleans decode, empty is None."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        wanted = {"gsm", "published_gse", *METADATA_V2_FIELDS, *METADATA_V2_OVERRIDES}
        if missing := wanted - set(reader.fieldnames or []):
            raise IntegrityError(f"{path.name} lacks metadata v2 columns: {sorted(missing)}")
        records = []
        for number, raw in enumerate(reader, 2):
            record: dict[str, Any] = {k: (raw[k] if raw[k] != "" else None) for k in wanted}
            for key in METADATA_V2_BOOLEANS:
                if record[key] is not None:
                    if record[key] not in {"True", "False"}:
                        raise IntegrityError(f"{path.name} line {number}: {key} is not True/False")
                    record[key] = record[key] == "True"
            confidence = None
            if record["annotation_confidence"]:
                try:
                    scores = json.loads(record["annotation_confidence"])
                    confidence = min(float(v) for v in scores.values() if v is not None) if scores else None
                except (ValueError, TypeError, AttributeError) as exc:
                    raise IntegrityError(f"{path.name} line {number}: invalid annotation_confidence") from exc
            record["annotation_min_confidence"] = confidence
            records.append(record)
    return records


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise IntegrityError(f"{path.name} line {number} is not a JSON object")
                records.append(value)
    return records


@dataclass
class Catalog:
    """A version-bound catalog; rows preserve the release's original annotations.

    `Catalog.open()` also joins study titles/summaries and reported biology (disease,
    condition, age, sex, donor, publication) from the release's atlas metadata files.
    Selections (`search`, `select`, `head`) return new catalogs bound to the same commit.
    """

    rows: list[dict[str, Any]]
    repo_id: str = DEFAULT_REPO
    revision: str = DEFAULT_REVISION
    catalog_sha256: str = ""
    local_files_only: bool = False
    cache_dir: Path | None = None
    studies: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    release: Release | None = None
    sample_records: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    source: str | None = None
    scores: dict[str, float] | None = field(default=None, repr=False)
    warnings: list[str] = field(default_factory=list, repr=False)
    report: dict[str, Any] | None = field(default=None, repr=False)
    gene_matches: dict[str, list[str]] | None = field(default=None, repr=False)
    metadata_v2: dict[str, Any] | None = field(default=None, repr=False)
    _shared: dict[str, Any] | None = field(default=None, repr=False)
    _by_gsm: dict[str, dict[str, Any]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not COMMIT.fullmatch(self.revision):
            raise ValueError("A resolved, full 40-character commit is required")
        for row in self.rows:
            validate_row(row)
        self._by_gsm = {row["gsm"]: row for row in self.rows}
        if len(self._by_gsm) != len(self.rows):
            raise ValueError("Catalog contains duplicate GSMs")
        if self._shared is None:
            self._shared = {"rows": self.rows}

    # ------------------------------------------------------------------ loading
    @classmethod
    def open(cls, release: str | None = None, *, source: str = "auto", offline: bool = False,
             cache_dir: str | Path | None = None, repo_id: str | None = None,
             metadata: bool = True) -> Catalog:
        """Open a release catalog: named release, alias, tag, 'latest' or full commit.

        source='auto' reads catalog files from the ungated public mirror when the release
        has one (searching needs no access approval) and from the gated dataset otherwise.
        Recorded SHA256 values bind mirror files to the release. Sample downloads always
        use the gated dataset at the release commit.
        """
        if source not in {"auto", "public", "gated"}:
            raise ValueError("source must be 'auto', 'public' or 'gated'")
        rel = resolve_release(release, repo_id=repo_id, offline=offline)
        if source == "public" and not rel.public_repo_id:
            raise NotFoundError(f"Release {rel.name} has no public catalog mirror",
                                hint="Use source='gated' (or 'auto') with approved dataset access.")
        public = bool(rel.public_repo_id) and source in {"auto", "public"}
        repo, revision = (rel.public_repo_id, rel.public_revision) if public else (rel.repo_id, rel.revision)
        cache = Path(cache_dir).expanduser() if cache_dir is not None else None

        def fetch(filename: str, optional: bool = False, location: tuple[str, str] | None = None) -> Path | None:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import EntryNotFoundError, LocalEntryNotFoundError

            where_repo, where_revision = location or (repo, revision)
            with hub_errors():
                try:
                    path = Path(hf_hub_download(where_repo, filename, repo_type="dataset", revision=where_revision,
                                                local_files_only=offline, cache_dir=cache))
                except LocalEntryNotFoundError:
                    if optional and offline:
                        return None
                    raise
                except EntryNotFoundError:
                    if optional:
                        return None
                    raise
            if location is not None:
                return path  # the caller checks the hash recorded for this file
            expected = rel.files.get(filename)
            if public and not expected:
                raise IntegrityError(f"Release {rel.name} records no SHA256 for mirrored {filename}",
                                     hint="Use source='gated', or a client version that records this file.")
            if expected and sha256_file(path) != expected:
                raise IntegrityError(f"{filename} from {repo}@{revision[:7]} differs from release {rel.name}",
                                     hint="Clear the cached file and retry; report the release name if it persists.")
            return path

        catalog_path = fetch(CATALOG_FILE)
        assert catalog_path is not None
        result = cls.from_csv(catalog_path, revision=rel.revision, repo_id=rel.repo_id,
                              local_files_only=offline, cache_dir=cache)
        result.release = rel
        result.source = f"{repo}@{revision}"
        if rel.subset:  # a named subset of the release, e.g. the no-login starter pack
            subset_path = fetch(rel.subset["path"])
            if sha256_file(subset_path) != rel.subset["sha256"]:
                raise IntegrityError(f"{rel.subset['path']} differs from release {rel.name}")
            members = json.loads(subset_path.read_text(encoding="utf-8"))["gsms"]
            rows = [result._by_gsm[g] for g in members]
            result = cls(rows, result.repo_id, result.revision, result.catalog_sha256, offline, cache)
            result.release = rel
            result.source = f"{repo}@{revision}"
        if metadata:
            study_path = fetch(STUDY_METADATA_FILE, optional=True)
            sample_path = fetch(SAMPLE_METADATA_FILE, optional=True)
            if study_path or sample_path:
                result._attach_metadata(_read_jsonl(study_path) if study_path else None,
                                        _read_jsonl(sample_path) if sample_path else None)
            if rel.metadata:
                info = rel.metadata
                location = ((info["repo_id"], info["revision"]) if source in {"auto", "public"}
                            else (info.get("gated_repo_id", rel.repo_id), info.get("gated_revision", rel.revision)))
                v2_path = fetch(info["path"], location=location)
                assert v2_path is not None
                if sha256_file(v2_path) != info["sha256"]:
                    raise IntegrityError(f"{info['path']} from {location[0]}@{location[1][:7]} differs from the "
                                         f"metadata recorded for release {rel.name}",
                                         hint="Clear the cached file and retry, or use --release paper "
                                              "(v3 metadata only); report the release name if it persists.")
                result._attach_metadata_v2(read_metadata_v2(v2_path), info)
        return result

    @classmethod
    def from_csv(cls, path: str | Path, *, revision: str, repo_id: str = DEFAULT_REPO,
                 local_files_only: bool = False, cache_dir: str | Path | None = None) -> Catalog:
        """Read a catalog whose fixed Hub commit is supplied explicitly by the caller."""
        path = Path(path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if missing := REQUIRED - set(reader.fieldnames or []):
                raise ValueError(f"Missing catalog columns: {sorted(missing)}")
            rows = [_decode_row(row) for row in reader]
        return cls(rows, repo_id, revision, sha256_file(path), local_files_only,
                   Path(cache_dir) if cache_dir is not None else None)

    @classmethod
    def from_hub(cls, *, revision: str = DEFAULT_REVISION, repo_id: str = DEFAULT_REPO,
                 local_files_only: bool = False, cache_dir: str | Path | None = None) -> Catalog:
        """Read only catalog/samples.csv from the gated dataset (client 0.1-0.2 behaviour).

        Prefer `Catalog.open()`, which adds study and biology metadata and needs no
        approval to search the paper release.
        """
        if not COMMIT.fullmatch(revision) and local_files_only:
            raise ValueError("Offline mode requires a full commit hash; resolve refs online first")
        return cls.open(revision, source="gated", offline=local_files_only, cache_dir=cache_dir,
                        repo_id=repo_id, metadata=False)

    @classmethod
    def from_selection(cls, path: str | Path, **open_options: Any) -> Catalog:
        """Re-open the commit recorded in a saved selection and select the same GSMs."""
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if record.get("schema") not in {"sda-db-selection-1", "sda-db-selection-2"}:
            raise ValueError("Unsupported selection file")
        from .releases import recorded_release

        catalog = cls.open(recorded_release(record), repo_id=record.get("repo_id"), **open_options)
        if record.get("catalog_sha256") and catalog.catalog_sha256 != record["catalog_sha256"]:
            raise IntegrityError("Catalog bytes differ from the selection record")
        return catalog.select(row["gsm"] for row in record["samples"])

    def _attach_metadata(self, studies: list[dict[str, Any]] | None,
                         samples: list[dict[str, Any]] | None) -> None:
        if studies is not None:
            self.studies = {str(s["gse"]): s for s in studies if s.get("gse")}
        if samples is not None:
            self.sample_records = {str(s["gsm"]): s for s in samples if s.get("gsm")}
        for row in self.rows:
            study = self.studies.get(row["published_gse"], {})
            record = self.sample_records.get(row["gsm"], {})
            row["study_title"] = _text(study.get("title"))
            for key in METADATA_FIELDS[1:]:
                row[key] = _text(record.get(key))
        self._shared = {"rows": self.rows}

    def _attach_metadata_v2(self, records: list[dict[str, Any]], info: dict[str, Any]) -> None:
        """Join curated metadata v2 by gsm; v3 values of corrected columns are kept as <name>_v3."""
        by_gsm: dict[str, dict[str, Any]] = {}
        for record in records:
            if record["gsm"] in by_gsm:
                raise IntegrityError(f"Metadata v2 lists {record['gsm']} twice")
            by_gsm[record["gsm"]] = record
        missing = [row["gsm"] for row in self.rows if row["gsm"] not in by_gsm]
        if missing:
            raise IntegrityError(f"Metadata v2 lacks {len(missing)} samples of this catalog, e.g. {missing[0]}",
                                 hint="Use --release paper for the v3 metadata alone.")
        for row in self.rows:
            record = by_gsm[row["gsm"]]
            if record["published_gse"] != row["published_gse"]:
                raise IntegrityError(f"Metadata v2 places {row['gsm']} in {record['published_gse']}, "
                                     f"the catalog in {row['published_gse']}")
            for name in METADATA_V2_OVERRIDES:
                row[f"{name}_v3"] = row.get(name) if row.get(name) != "" else None
                row[name] = record[name]
            for name in METADATA_V2_FIELDS:
                row[name] = record[name]
            row["annotation_min_confidence"] = record["annotation_min_confidence"]
        self.metadata_v2 = dict(info)
        self._shared = {"rows": self.rows}

    # ---------------------------------------------------------------- basics
    def _derive(self, rows: list[dict[str, Any]], **changes: Any) -> Catalog:
        derived = Catalog(rows, self.repo_id, self.revision, self.catalog_sha256, self.local_files_only,
                          self.cache_dir, self.studies, self.release, self.sample_records, self.source,
                          metadata_v2=self.metadata_v2, _shared=self._shared, **changes)
        return derived

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)

    def __contains__(self, gsm: object) -> bool:
        return gsm in self._by_gsm

    def __repr__(self) -> str:
        name = self.release.name if self.release else self.revision[:7]
        studies = len({row["published_gse"] for row in self.rows})
        return f"<Catalog release={name} commit={self.revision[:7]} samples={len(self.rows)} studies={studies}>"

    def _repr_html_(self) -> str:
        from html import escape

        head = "".join(f"<th>{escape(f)}</th>" for f in self.summary_fields)
        body = "".join("<tr>" + "".join(f"<td>{escape(str(r.get(f, '') if r.get(f) is not None else ''))}</td>"
                                        for f in self.summary_fields) + "</tr>" for r in self.rows[:20])
        more = f"<p>… {len(self.rows) - 20} more rows</p>" if len(self.rows) > 20 else ""
        return f"<p>{escape(repr(self))}</p><table><tr>{head}</tr>{body}</table>{more}"

    @property
    def release_name(self) -> str:
        return self.release.name if self.release else self.revision[:7]

    @property
    def summary_fields(self) -> tuple[str, ...]:
        """Compact columns: SUMMARY_FIELDS, plus organ/sample kind/disease/assay with metadata v2."""
        if not self.metadata_v2:
            return SUMMARY_FIELDS
        cut = SUMMARY_FIELDS.index("disease")
        return SUMMARY_FIELDS[:cut] + V2_SUMMARY_FIELDS + SUMMARY_FIELDS[cut + 1:]

    @property
    def gsms(self) -> list[str]:
        return [row["gsm"] for row in self.rows]

    @property
    def field_names(self) -> list[str]:
        names: dict[str, None] = {}
        for row in self.rows[:1] or self._shared["rows"][:1]:
            names.update(dict.fromkeys(row))
        return list(names)

    def get(self, gsm: str) -> dict[str, Any]:
        """Return a canonical GSM row, failing clearly for missing or alias-only GSMs."""
        try:
            return dict(self._by_gsm[gsm])
        except KeyError:
            alias_of = next((r["gsm"] for r in self._shared["rows"] if gsm in members("aliases", r.get("aliases"))),
                            None)
            hint = (f"{gsm} is an alias of {alias_of}, which holds the same content." if alias_of else
                    "Check the ID, the release ('sda-db releases') and use 'sda-db search' to find canonical GSMs.")
            raise NotFoundError(f"GSM {gsm!r} is absent from this catalog; check canonical IDs and aliases",
                                hint=hint) from None

    def select(self, gsms: Iterable[str]) -> Catalog:
        """Rows for the given GSMs, in the given order; unknown IDs fail before any work."""
        if isinstance(gsms, str):
            gsms = [gsms]
        identifiers = list(dict.fromkeys(gsms))
        missing = [g for g in identifiers if g not in self._by_gsm]
        if len(missing) == 1:
            self.get(missing[0])  # raises with an alias-aware hint
        if missing:
            raise NotFoundError(f"{len(missing)} GSMs are absent from this catalog: {', '.join(missing[:10])}",
                                hint="Search the release for canonical GSM IDs.")
        return self._derive([dict(self._by_gsm[g]) for g in identifiers])

    def head(self, n: int = 10) -> Catalog:
        return self._derive([dict(r) for r in self.rows[:n]], scores=self.scores, warnings=list(self.warnings),
                            report=self.report, gene_matches=self.gene_matches)

    # ---------------------------------------------------------------- search
    def _vocabulary(self) -> Vocabulary:
        if "vocabulary" not in self._shared:
            if self.metadata_v2:
                self._shared["vocabulary"] = Vocabulary(self._shared["rows"], self.field_names,
                                                        controlled=CONTROLLED_FIELDS | V2_CONTROLLED_FIELDS,
                                                        aliases={**VALUE_ALIASES, **V2_VALUE_ALIASES})
            else:
                self._shared["vocabulary"] = Vocabulary(self._shared["rows"], self.field_names)
        return self._shared["vocabulary"]

    def _index(self) -> TextIndex:
        if "index" not in self._shared:
            self._shared["index"] = TextIndex(self._shared["rows"], self.studies)
        return self._shared["index"]

    def feature_index(self, path: str | Path | None = None) -> Any:
        """The release's measured-feature index (downloaded once, SHA256-checked; see sda_db.features)."""
        from .features import load_feature_index

        key = ("features", str(path) if path else None)
        if key not in self._shared:
            self._shared[key] = load_feature_index(self, path)
        return self._shared[key]

    def features(self, gsm: str, *, feature_index: str | Path | None = None) -> dict[str, Any]:
        """Features measured in one sample, without downloading it.

        Returns {'features': n, 'feature_types': {...}, 'listed': bool, 'rows': [...] or None}; full rows are
        stored for targeted panels (up to 10,000 features), counts for whole-transcriptome panels.
        """
        self.get(gsm)
        index = self.feature_index(feature_index)
        info = index.panel_info(gsm)
        return {**info, "rows": index.features(gsm) if info["listed"] else None}

    def search(self, query: str | None = None, *, where: str | Sequence[str] | None = None,
               text: str | None = None, min_obs: int | None = None, max_obs: int | None = None,
               match: str = "all", sort: str | None = None, genes: str | Sequence[str] | None = None,
               gene_match: str = "all", feature_index: str | Path | None = None,
               min_confidence: float | None = None, exclude_flags: bool | str | Sequence[str] | None = None,
               **filters: Any) -> Catalog:
        """Select samples by ranked keywords and structured filters (all ANDed).

        - ``query``: keywords over sample fields (tissue, disease, organism, platform, IDs)
          and the study title/design/summary. Every term must match unless
          ``match='any'``; ``"quoted phrases"``, ``-excluded`` and ``prefix*`` terms work.
          Terms that match nothing in the release are ignored with a warning.
          Results are ranked by BM25 (``catalog.scores``).
        - ``filters``: exact values, case/space/hyphen-insensitive; a list means OR.
          Common names work for organism ('human') and platform ('visium hd').
          Multi-valued fields (gse, organism, flags, qc_flags, aliases) match any member.
        - ``where``: expressions such as ``'n_obs>=1000'``, ``'tissue~cortex'``,
          ``'platform!=Visium'``, ``'disease='`` (missing) or ``'split=train|val'``.
        - ``text``: literal substring over all catalog values (client 0.1 behaviour).
        - ``genes``: samples whose measured features include all (``gene_match='any'``: any) of these
          symbols or IDs, case-insensitively, from the release's feature index. Measured is not detected,
          and whole-transcriptome assays measure almost every gene.

        With curated metadata v2 (the default release; not the paper release):

        - ``organ``, ``disease_status``, ``sample_kind``, ``life_stage``, ``sex``, ``assay_type``,
          ``assay_name``, ``preservation`` filter the harmonized values (``catalog.values(field)`` lists them;
          common words such as 'tumor', 'healthy', 'frozen' or 'imaging' work).
        - ``disease``: a MONDO ID ('MONDO:0005184') or a MONDO name, exactly or as a case-insensitive
          substring ('fibrosis'); ancestor terms are not expanded. ``where='disease~TEXT'`` still searches
          the disease text as reported in GEO.
        - ``organism``: scientific name, NCBI common name, taxon ID or 'human'/'mouse'; any listed host
          species matches.
        - ``min_confidence``: keep samples whose lowest annotation confidence (organ, sample kind, disease
          status, UBERON term, life stage) is at least this value (0-1; 0.6 drops every low-confidence call).
        - ``exclude_flags``: True drops every sample with a ``metadata_flags`` entry; a flag name or list
          drops only those (e.g. 'normal_but_disease_text', 'feature_species_differs').

        This is deterministic metadata search; it makes no model calls.
        """
        if match not in {"all", "any"}:
            raise ValueError("match must be 'all' or 'any'")
        vocabulary = self._vocabulary()
        warnings: list[str] = []
        conditions: list[Condition] = []
        special: list[tuple[Condition, Any]] = []
        for key, value in filters.items():
            if self.metadata_v2 and key in {"disease", "organism"}:
                values = tuple(value) if isinstance(value, (list, tuple, set, frozenset)) else (value,)
                build = disease_term_predicate if key == "disease" else organism_predicate
                special.append((Condition(key, "=", values), build(values, vocabulary, warnings)))
                continue
            if key not in vocabulary.fields and key in METADATA_V2_ONLY:
                raise UnknownFieldError(f"{key!r} comes with curated metadata v2, which release "
                                        f"{self.release_name} does not include",
                                        hint="Open the default release (v1.1.0-20261105, alias metadata-v2); "
                                             "the paper release keeps its original v3 metadata.")
            if key not in vocabulary.fields:
                close = difflib.get_close_matches(key, vocabulary.fields, n=1, cutoff=0.6)
                raise UnknownFieldError(f"Unsupported filters: {key!r}",
                                        hint=(f"Did you mean {close[0]!r}? " if close else "")
                                        + "List fields with Catalog.fields() or 'sda-db fields'.")
            if key in BOOLEAN_FIELDS | QUERY_BOOLEANS and not (
                    type(value) is bool or (isinstance(value, (list, tuple)) and all(type(v) is bool for v in value))):
                raise UnknownValueError(f"{key} requires a boolean (True or False)")
            conditions.append(condition_from_kwarg(key, value))
        if isinstance(where, str):
            where = [where]
        conditions.extend(parse_where(expression) for expression in (where or []))
        if (min_confidence is not None or exclude_flags not in (None, False)) and not self.metadata_v2:
            raise UnknownFieldError(f"min_confidence and exclude_flags need curated metadata v2, which release "
                                    f"{self.release_name} does not include",
                                    hint="Open the default release (v1.1.0-20261105, alias metadata-v2).")
        if min_confidence is not None:
            if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) \
                    or not 0 <= min_confidence <= 1:
                raise UnknownValueError("min_confidence must be a number from 0 to 1", hint="Example: 0.6")
            conditions.append(Condition("annotation_min_confidence", ">=", (float(min_confidence),)))
        if exclude_flags is True or (isinstance(exclude_flags, str) and exclude_flags.strip().casefold()
                                     in {"any", "all", "true"}):
            conditions.append(Condition("metadata_flags", "=", ("",)))
        elif exclude_flags not in (None, False):
            flags = re.split(r"[,\s]+", exclude_flags) if isinstance(exclude_flags, str) else list(exclude_flags)
            flags = [f for f in flags if f]
            if flags:
                conditions.append(Condition("metadata_flags", "!=", tuple(flags)))
        if min_obs is not None and max_obs is not None and min_obs > max_obs:
            raise ValueError("min_obs must be <= max_obs")
        if min_obs is not None:
            conditions.append(Condition("n_obs", ">=", (min_obs,)))
        if max_obs is not None:
            conditions.append(Condition("n_obs", "<=", (max_obs,)))
        predicates = [compile_condition(c, vocabulary, warnings) for c in conditions]
        predicates += [predicate for _, predicate in special]
        conditions = [condition for condition, _ in special] + conditions
        selected = [row for row in self.rows if all(p(row) for p in predicates)]
        if text:
            needle = text.casefold()
            selected = [row for row in selected
                        if needle in " ".join(str(v) for v in row.values() if v is not None).casefold()]
        gene_matches: dict[str, list[str]] | None = None
        if genes:
            wanted = [genes] if isinstance(genes, str) else list(genes)
            wanted = [g for item in wanted for g in re.split(r"[,\s]+", str(item)) if g]
            index = self.feature_index(feature_index)
            gene_matches, gene_counts = index.match(wanted, (r["gsm"] for r in selected), mode=gene_match)
            unindexed = sum(1 for r in selected if r["gsm"] not in index.samples)
            if unindexed:
                warnings.append(f"{unindexed} selected samples are not in the feature index (e.g. daily additions "
                                "after the indexed release) and were left out of the gene filter")
            for gene, count in gene_counts.items():
                if not index.panels_with(gene):
                    close = index.suggest(gene)
                    warnings.append(f"No sample measures {gene!r}" + (f"; similar: {', '.join(close)}" if close else ""))
            selected = [r for r in selected if r["gsm"] in gene_matches]
            conditions.append(Condition("genes", "=" if gene_match == "all" else "~", tuple(wanted)))
        scores: dict[str, float] | None = None
        report: dict[str, Any] = {"filters": [c.describe() for c in conditions], "query": query,
                                  "matched_before_query": len(selected)}
        if gene_matches is not None:
            report["gene_counts"] = gene_counts
        if query and query.strip():
            scores, text_report = self._index().search(query, within=(r["gsm"] for r in selected), match=match)
            report.update(text_report)
            warnings.extend(text_report.get("notes", []))
            if text_report["all_ignored"] and not text_report["excluded"]:
                # No keyword matched anything: fall back to a literal substring (client 0.1-0.2 behaviour),
                # so partial words ("hippo") and catalog codes ("VERIFIED") still work.
                needle = query.strip().casefold()
                literal = [row for row in selected if needle in " ".join(
                    str(v) for v in row.values() if v is not None).casefold()]
                scores = None
                if literal:
                    warnings.append(f"No keyword matched; used a literal substring match for {query.strip()!r}.")
                else:
                    warnings.append("Ignored terms with no match in this release: "
                                    + ", ".join(text_report["ignored_terms"]))
                selected = literal
            elif not text_report["operative"]:
                warnings.append("The query had only generic words; no keyword filter was applied.")
                scores = None
            else:
                if text_report["ignored_terms"]:
                    warnings.append("Ignored terms with no match in this release: "
                                    + ", ".join(text_report["ignored_terms"]))
                order = {row["gsm"]: i for i, row in enumerate(selected)}
                selected = sorted((r for r in selected if r["gsm"] in scores),
                                  key=lambda r: (-scores[r["gsm"]], order[r["gsm"]]))
                if not (text_report["terms"] or text_report["phrases"]):
                    scores = None  # exclusions only: filtered, not ranked
        if sort:
            selected = _sorted(selected, sort, vocabulary)
        report["total"] = len(selected)
        if not selected and (filters or where or query or genes or min_confidence is not None or exclude_flags):
            warnings.append("No samples matched. Relax a filter, use FIELD~TEXT substrings, "
                            "or inspect values with 'sda-db values FIELD'.")
        return self._derive([dict(r) for r in selected], scores=scores, warnings=warnings, report=report,
                            gene_matches=gene_matches if gene_matches is not None else self.gene_matches)

    # ---------------------------------------------------------------- describing
    def fields(self) -> list[dict[str, Any]]:
        """Every searchable field with its meaning, kind, distinct count and top values."""
        docs = field_docs(bool(self.metadata_v2))
        vocabulary = self._vocabulary()
        result = []
        for name in self.field_names:
            counter = vocabulary.values(name)
            kind = ("boolean" if name in BOOLEAN_FIELDS | QUERY_BOOLEANS else "number" if name in NUMERIC_FIELDS
                    else "id" if name in {"gsm", "published_gse", "sample_dir", "expression_sha256", "image_sha256",
                                          "content_group", "review_evidence_sha256", "publication_pmid",
                                          "publication_doi", "donor_id", "donor_key", "organism_taxon_id",
                                          "tissue_ontology_id"}
                    else "list" if name in LIST_SEPARATORS and name not in vocabulary.controlled
                    else "category" if name in vocabulary.controlled else "text")
            missing = sum(1 for r in self._shared["rows"] if r.get(name) in (None, ""))
            entry = {"field": name, "kind": kind, "description": docs.get(name, ""), "distinct": len(counter),
                     "missing": missing}
            if kind in {"category", "text", "list", "boolean"}:
                entry["top_values"] = [v for v, _ in counter.most_common(5)]
            result.append(entry)
        return result

    def values(self, field_name: str, *, top: int | None = None) -> list[dict[str, Any]]:
        """Distinct values of a field in this selection with sample/study/observation counts.

        Multi-valued fields count each member; booleans are True/False; missing is None.
        """
        self._vocabulary().check_field(field_name)
        samples: Counter[Any] = Counter()
        studies: dict[Any, set[str]] = defaultdict(set)
        observations: Counter[Any] = Counter()
        for row in self.rows:
            for item in _facet_items(field_name, row):
                samples[item] += 1
                studies[item].add(row["published_gse"])
                observations[item] += row["n_obs"]
        ordered = sorted(samples, key=lambda v: (-samples[v], v is None, str(v)))
        if top is not None:
            ordered = ordered[:top]
        return [{"value": v, "samples": samples[v], "studies": len(studies[v]), "observations": observations[v]}
                for v in ordered]

    def summary(self, by: str | Sequence[str] | None = None) -> dict[str, Any]:
        """Totals for this selection, optionally cross-tabulated by one or more fields."""
        rows = self.rows
        totals = {"samples": len(rows), "studies": len({r["published_gse"] for r in rows}),
                  "observations": sum(r["n_obs"] for r in rows),
                  "with_image": sum(1 for r in rows if r["has_image"]),
                  "platforms": len({r.get("platform") for r in rows if r.get("platform")}),
                  "organisms": len({m for r in rows for m in members("organism", r.get("organism"))})}
        result: dict[str, Any] = {"release": self.release_name, "commit": self.revision, "totals": totals}
        if by:
            keys = [k.strip() for k in ([by] if isinstance(by, str) else list(by)) if k.strip()]
            for key in keys:
                self._vocabulary().check_field(key)
            groups: dict[tuple, dict[str, Any]] = {}
            for row in rows:
                options = [_facet_items(k, row) for k in keys]
                combos: list[tuple] = [()]
                for values in options:
                    combos = [c + (v,) for c in combos for v in values]
                for combo in combos:
                    group = groups.setdefault(combo, {"samples": 0, "studies": set(), "observations": 0,
                                                      "with_image": 0})
                    group["samples"] += 1
                    group["studies"].add(row["published_gse"])
                    group["observations"] += row["n_obs"]
                    group["with_image"] += int(row["has_image"])
            result["by"] = keys
            result["groups"] = [{**dict(zip(keys, combo)), "samples": g["samples"], "studies": len(g["studies"]),
                                 "observations": g["observations"], "with_image": g["with_image"]}
                                for combo, g in sorted(groups.items(), key=lambda item: -item[1]["samples"])]
        return result

    def list_studies(self) -> list[dict[str, Any]]:
        """One record per published series in this selection, largest first."""
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.rows:
            grouped[row["published_gse"]].append(row)
        result = []
        for gse, rows in grouped.items():
            study = self.studies.get(gse, {})
            result.append({"published_gse": gse, "title": study.get("title"), "samples": len(rows),
                           "observations": sum(r["n_obs"] for r in rows),
                           "with_image": sum(1 for r in rows if r["has_image"]),
                           "platforms": sorted({r.get("platform") for r in rows if r.get("platform")}),
                           "organisms": sorted({m for r in rows for m in members("organism", r.get("organism"))}),
                           "splits": sorted({r["split"] for r in rows}),
                           "pubmed_ids": study.get("pubmed_ids") or []})
        return sorted(result, key=lambda s: (-s["samples"], s["published_gse"]))

    def study(self, gse: str) -> dict[str, Any]:
        """Study metadata, aggregate counts and compact sample rows for one series."""
        rows = [r for r in self._shared["rows"] if gse in members("gse", r["gse"])]
        if not rows:
            raise NotFoundError(f"Series {gse!r} has no samples in this release",
                                hint="Search with a keyword or check the accession.")
        selection = self._derive([dict(r) for r in rows])
        aggregate = selection.list_studies()
        published = sorted({r["published_gse"] for r in rows})
        meta = self.studies.get(gse) or (self.studies.get(published[0], {}) if len(published) == 1 else {})
        pubmed = sorted({str(p) for s in aggregate for p in s["pubmed_ids"]} | {str(p) for p in meta.get("pubmed_ids") or []})
        return {"gse": gse, "published_gse": published[0] if len(published) == 1 else published,
                "title": meta.get("title") or (aggregate[0]["title"] if len(aggregate) == 1 else None),
                "samples": len(rows), "observations": sum(r["n_obs"] for r in rows),
                "with_image": sum(1 for r in rows if r["has_image"]),
                "platforms": sorted({r.get("platform") for r in rows if r.get("platform")}),
                "organisms": sorted({m for r in rows for m in members("organism", r.get("organism"))}),
                "splits": sorted({r["split"] for r in rows}), "pubmed_ids": pubmed,
                "summary": meta.get("summary"), "design": meta.get("design"), "geo_url": GEO_URL.format(gse),
                "member_studies": [{"published_gse": s["published_gse"], "title": s["title"], "samples": s["samples"]}
                                   for s in aggregate] if len(aggregate) > 1 else None,
                "samples_detail": [{k: r.get(k) for k in self.summary_fields if k != "study_title"} for r in rows]}

    def card(self, gsm: str) -> dict[str, Any]:
        """Everything the catalog knows about one sample, grouped for reading."""
        row = self.get(gsm)
        record = self.sample_records.get(gsm, {})
        study = self.studies.get(row["published_gse"], {})
        pick = lambda *keys: {k: row.get(k) for k in keys if k in row}  # noqa: E731
        return {
            "gsm": gsm, "release": self.release_name, "commit": self.revision,
            "identity": pick("gsm", "gse", "published_gse", "sample_dir", "content_group", "group_kind", "aliases"),
            "study": {"title": study.get("title"), "design": study.get("design"),
                      "pubmed_ids": study.get("pubmed_ids") or [], "geo_url": GEO_URL.format(row["published_gse"])},
            "biology": pick("organism", "tissue", "disease", "condition", "age", "sex", "donor_id"),
            "assay": pick("platform", "technology", "observation_unit", "n_obs", "n_vars"),
            "expression": {**pick("expression_state", "verification", "counts_ready", "drop_features"),
                           "counts_evidence": record.get("counts_evidence"), "audit": record.get("audit")},
            "coordinates": pick("coordinate_units", "um_basis", "um_per_unit", "has_spatial_um"),
            "image": pick("has_image", "image_stain", "image_stain_basis", "image_um_per_px", "image_registration"),
            "subcellular": pick("segmentation", "molecules", "molecules_use"),
            "quality": pick("flags", "qc_flags", "review_decision", "review_category", "review_notes",
                            "review_kind", "frozen_paper_evaluation_eligible"),
            "split": row["split"],
            **({"curated": self._curated(row)} if self.metadata_v2 else {}),
            "provenance": pick("source_pipeline", "source_release"),
            "publication": pick("publication_pmid", "publication_doi"),
            "files": {"sample.json": f"{row['sample_dir']}/sample.json",
                      "spatial.h5ad": f"{row['sample_dir']}/spatial.h5ad",
                      "image.tif": f"{row['sample_dir']}/image.tif" if row["has_image"] else None,
                      "expression_sha256": row["expression_sha256"],
                      "image_sha256": row["image_sha256"] or None},
        }

    def _curated(self, row: dict[str, Any]) -> dict[str, Any]:
        """Metadata v2 of one sample, grouped; the release's own (v3) values stay in the other sections."""
        pick = lambda *keys: {k: row.get(k) for k in keys}  # noqa: E731
        listed = lambda key: members(key, row.get(key))  # noqa: E731
        confidence = row.get("annotation_confidence")
        info = self.metadata_v2 or {}
        return {
            "metadata": f"{info.get('schema', METADATA_V2_SCHEMA)} ({info.get('tag')})",
            "tissue": {**pick("organ", "organ_source", "tissue_ontology_id", "tissue_ontology_name", "sample_kind")},
            "disease": {"disease_status": row.get("disease_status"),
                        "disease_status_source": row.get("disease_status_source"),
                        "disease_ontology_ids": listed("disease_ontology_ids"),
                        "disease_ontology_names": listed("disease_ontology_names")},
            "donor": pick("life_stage", "life_stage_source", "sex", "donor_key"),
            "species": {**pick("organism", "organism_taxon_id", "organism_common_name", "feature_species",
                               "xenograft", "multi_species"),
                        "organisms": listed("organisms"), "co_detected_organisms": listed("co_detected_organisms")},
            "assay": pick("platform", "platform_v3", "platform_basis", "assay_name", "assay_type", "preservation",
                          "panel_name"),
            "publications": {"pubmed_ids": listed("pubmed_ids"), "dois": listed("dois")},
            "annotation": {"annotation_version": row.get("annotation_version"),
                           "confidence": json.loads(confidence) if confidence else None,
                           "min_confidence": row.get("annotation_min_confidence"),
                           "low_confidence": listed("annotation_low_confidence"),
                           "metadata_flags": listed("metadata_flags")},
        }

    # ---------------------------------------------------------------- export
    def to_records(self, fields: Sequence[str] | str | None = None) -> list[dict[str, Any]]:
        """Rows as dictionaries; fields=None/'all' keeps every column, 'summary' the compact set."""
        if fields in (None, "all"):
            chosen = None
        elif fields == "summary":
            chosen = list(self.summary_fields)
        else:
            chosen = [fields] if isinstance(fields, str) else list(fields)
            for name in chosen:
                if name != "score":
                    self._vocabulary().check_field(name)
        records = []
        for row in self.rows:
            record = dict(row) if chosen is None else {k: row.get(k) for k in chosen if k != "score"}
            if self.scores is not None and (chosen is None or "score" in chosen or fields == "summary"):
                record["score"] = self.scores.get(row["gsm"])
            records.append(record)
        return records

    def compact(self, limit: int | None = 20) -> dict[str, Any]:
        """Token-lean rows for agents: summary fields without empty values; study titles once."""
        samples, studies = [], {}
        for row in self.rows[:limit]:
            record = {k: row.get(k) for k in self.summary_fields if k != "study_title" and row.get(k) not in (None, "")}
            if self.scores is not None:
                record["score"] = self.scores.get(row["gsm"])
            if self.gene_matches is not None:
                record["matched_features"] = self.gene_matches.get(row["gsm"])
            samples.append(record)
            studies.setdefault(row["published_gse"], row.get("study_title"))
        return {"samples": samples, "studies": studies}

    def to_pandas(self, fields: Sequence[str] | str | None = None) -> Any:
        """A pandas DataFrame of the selection (requires pandas)."""
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas or 'spatial-data-agent-db[analysis]' for DataFrames") from exc
        frame = pd.DataFrame(self.to_records(fields))
        return frame.set_index("gsm", drop=False) if "gsm" in frame else frame

    def export(self, path: str | Path, *, format: str | None = None,
               fields: Sequence[str] | str | None = None) -> Path:
        """Write rows as csv, tsv, jsonl, parquet, or a JSON selection record (by extension)."""
        path = Path(path)
        kind = (format or path.suffix.lstrip(".") or "json").lower()
        if kind == "json":
            self.save_selection(path)
            return path
        records = self.to_records(fields)
        if kind in {"csv", "tsv"}:
            names = list(dict.fromkeys(k for r in records for k in r)) if records else self.field_names
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=names, delimiter="," if kind == "csv" else "\t")
                writer.writeheader()
                writer.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in records)
        elif kind == "jsonl":
            with path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        elif kind == "parquet":
            self.to_pandas(fields).to_parquet(path, index=False)
        else:
            raise ValueError("format must be json, csv, tsv, jsonl or parquet")
        return path

    def save_selection(self, path: str | Path) -> None:
        """Export selected rows and provenance as a JSON record for sharing and replay."""
        from . import __version__

        Path(path).write_text(json.dumps({
            "schema": "sda-db-selection-2", "repo_id": self.repo_id, "revision": self.revision,
            "release": self.release_name, "catalog_sha256": self.catalog_sha256, "client_version": __version__,
            "query": (self.report or {}).get("query"), "filters": (self.report or {}).get("filters"),
            **({"metadata": {k: self.metadata_v2.get(k) for k in ("schema", "tag", "path", "sha256")}}
               if self.metadata_v2 else {}),
            "samples": self.rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- actions
    def plan_download(self, **options: Any) -> Any:
        """Bytes per sample and in total before anything is downloaded (see sda_db.batch)."""
        from .batch import plan_download

        return plan_download(self, **options)

    def download(self, destination: str | Path = "downloads", **options: Any) -> Any:
        """Download every sample of this selection in parallel (see sda_db.batch.download)."""
        from .batch import download

        return download(self, destination, **options)

    def stream(self, **options: Any) -> Any:
        """Iterate verified samples with bounded prefetch and a disk budget (see sda_db.stream)."""
        from .stream import stream_samples

        return stream_samples(self, **options)

    def cite(self, *, format: str = "bibtex") -> str:
        """Citations for the database and every original GEO series in this selection."""
        from .cite import cite

        return cite(self, format=format)


def _sorted(rows: list[dict[str, Any]], spec: str, vocabulary: Vocabulary) -> list[dict[str, Any]]:
    descending = spec.startswith("-")
    key = spec.lstrip("-+")
    vocabulary.check_field(key)

    present = [r for r in rows if r.get(key) not in (None, "")]
    absent = [r for r in rows if r.get(key) in (None, "")]
    if key in NUMERIC_FIELDS:
        present = sorted(present, key=lambda r: float(r[key]), reverse=descending)
    else:
        present = sorted(present, key=lambda r: str(r[key]).casefold(), reverse=descending)
    return present + absent  # Python's sort is stable, also with reverse=True: ties keep their rank


def _facet_items(field_name: str, row: dict[str, Any]) -> list[Any]:
    """Facet members of one row: booleans as booleans, lists split, missing as [None]."""
    value = row.get(field_name)
    if field_name in BOOLEAN_FIELDS | QUERY_BOOLEANS:
        if isinstance(value, bool):
            return [value]
        text = "" if value is None else str(value).casefold()
        return [True] if text == "true" else [False] if text == "false" else [None]
    return members(field_name, value) or [None]
