"""Catalog discovery without loading expression matrices or scientific dependencies."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

DEFAULT_REPO = "Biogod/SpatialDataAgent-DB"
DEFAULT_REVISION = "8f116b867ce4097957d4209db712d37bae3c9369"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BOOLEAN_FIELDS = frozenset({"has_image", "has_spatial_um"})
INTEGER_FIELDS = frozenset({"n_obs", "n_vars"})
FLOAT_FIELDS = frozenset({"um_per_unit", "image_um_per_px"})
FILTER_FIELDS = frozenset({"gsm", "gse", "published_gse", "platform", "organism", "tissue", "split",
                           "source_pipeline", "source_release", "observation_unit", "expression_state",
                           "verification", "coordinate_units", "um_basis", "image_stain", "image_registration",
                           "review_decision", "has_image", "has_spatial_um"})
REQUIRED = frozenset({"gsm", "gse", "published_gse", "sample_dir", "n_obs", "n_vars", "has_image",
                      "expression_sha256", "image_sha256", "expression_state", "coordinate_units", "split"})


def sha256_file(path: Path) -> str:
    """Hash files in bounded blocks, including large matrices and images."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    for key in INTEGER_FIELDS & row.keys():
        result[key] = int(row[key])
    for key in FLOAT_FIELDS & row.keys():
        result[key] = float(row[key]) if row[key] else None
    validate_row(result)
    return result


@dataclass
class Catalog:
    """A version-bound catalog; rows preserve the release's original annotations."""

    rows: list[dict[str, Any]]
    repo_id: str = DEFAULT_REPO
    revision: str = DEFAULT_REVISION
    catalog_sha256: str = ""
    local_files_only: bool = False
    cache_dir: Path | None = None
    _by_gsm: dict[str, dict[str, Any]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not COMMIT.fullmatch(self.revision):
            raise ValueError("A resolved, full 40-character commit is required")
        for row in self.rows:
            validate_row(row)
        self._by_gsm = {row["gsm"]: row for row in self.rows}
        if len(self._by_gsm) != len(self.rows):
            raise ValueError("Catalog contains duplicate GSMs")

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
        """Resolve a mutable ref once, then retrieve all files at the resulting commit."""
        from huggingface_hub import HfApi, hf_hub_download

        if not COMMIT.fullmatch(revision):
            if local_files_only:
                raise ValueError("Offline mode requires a full commit hash; resolve refs online first")
            revision = HfApi().repo_info(repo_id, repo_type="dataset", revision=revision).sha
        if not isinstance(revision, str) or not COMMIT.fullmatch(revision):
            raise ValueError("Hub did not return a full commit hash")
        path = hf_hub_download(repo_id, "catalog/samples.csv", repo_type="dataset", revision=revision,
                               local_files_only=local_files_only, cache_dir=cache_dir)
        return cls.from_csv(path, revision=revision, repo_id=repo_id,
                            local_files_only=local_files_only, cache_dir=cache_dir)

    def get(self, gsm: str) -> dict[str, Any]:
        """Return a canonical GSM row, failing clearly for missing or alias-only GSMs."""
        try:
            return dict(self._by_gsm[gsm])
        except KeyError as exc:
            raise ValueError(f"GSM {gsm!r} is absent from this catalog; check canonical IDs and aliases") from exc

    def search(self, text: str | None = None, *, min_obs: int | None = None,
               max_obs: int | None = None, **filters: Any) -> Catalog:
        """AND exact filters (case insensitive); text uses literal substring matching.

        GSE filters match any member of the '+'-separated release membership.
        This searches catalog metadata, without running an intent-search model.
        """
        if unknown := filters.keys() - FILTER_FIELDS:
            raise ValueError(f"Unsupported filters: {sorted(unknown)}")
        for key in BOOLEAN_FIELDS & filters.keys():
            if type(filters[key]) is not bool:
                raise ValueError(f"{key} requires a boolean")
        if min_obs is not None and max_obs is not None and min_obs > max_obs:
            raise ValueError("min_obs must be <= max_obs")
        selected = []
        for row in self.rows:
            if min_obs is not None and row["n_obs"] < min_obs:
                continue
            if max_obs is not None and row["n_obs"] > max_obs:
                continue
            if any(str(value).casefold() not in
                   ([v.casefold() for v in row["gse"].split("+")] if key == "gse" else
                    [str(row.get(key, "")).casefold()]) for key, value in filters.items()):
                continue
            if text and text.casefold() not in " ".join(str(v) for v in row.values()).casefold():
                continue
            selected.append(dict(row))
        return Catalog(selected, self.repo_id, self.revision, self.catalog_sha256,
                       self.local_files_only, self.cache_dir)

    def save_selection(self, path: str | Path) -> None:
        """Export selected rows and provenance as a JSON record for sharing and inspection."""
        Path(path).write_text(json.dumps({"schema": "sda-db-selection-1", "repo_id": self.repo_id,
                                        "revision": self.revision, "catalog_sha256": self.catalog_sha256,
                                        "samples": self.rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
