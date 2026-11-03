"""Measured-feature index: which samples measure a gene (or other feature), without opening matrices.

The index is a SQLite file built from every sample's `var` table. Identical feature lists form one panel.
Search keys map to the panels that contain them; each key keeps the feature names exactly as deposited.

- **Measured, not detected.** A gene in the index was measured; it was not necessarily detected.
- **Whole-transcriptome assays.** These measure almost every gene, so combine gene filters with
  `where='n_vars<=10000'` to find targeted panels.
- **Keys.** A key is the case-folded name or source ID, with Ensembl version suffixes and reference-genome
  prefixes (`GRCh38_`, `mm10_`…) removed.
- **What is searchable.** The index holds keys found in two or more panels, and every key of a targeted panel.
  Names unique to one whole-transcriptome panel are counted but not searchable.
- **Feature lists.** Full lists are stored for targeted panels (up to 10,000 features).
"""

from __future__ import annotations

from collections import Counter, defaultdict
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import tempfile
import zlib
from typing import Any, Iterable, Sequence

from .errors import IntegrityError, NotFoundError, hub_errors

SCHEMA = "sda-db-feature-index-3"
TARGETED_MAX = 10000
_ENSEMBL_VERSION = re.compile(r"^(ens[a-z]*[gtp][0-9]+)\.[0-9]+$")
_GENOME_PREFIX = re.compile(r"^(?:grch3[78]|hg19|hg38|grcm3[89]|mm10|mm39)[_\-]+")


def feature_keys(text: str) -> set[str]:
    """Search keys of one feature name or ID."""
    value = str(text).strip().casefold()
    if not value:
        return set()
    keys = {value, _GENOME_PREFIX.sub("", value)}
    for item in list(keys):
        match = _ENSEMBL_VERSION.match(item)
        if match:
            keys.add(match.group(1))
    return {k for k in keys if k}


def feature_key(text: str) -> str:
    """The key used to look up a query."""
    value = str(text).strip().casefold()
    match = _ENSEMBL_VERSION.match(value)
    return match.group(1) if match else value


def _encode_panels(panels: Iterable[int], total: int) -> bytes:
    numbers = sorted(set(panels))
    if 2 * len(numbers) < (total + 7) // 8:
        return b"L" + struct.pack(f"<{len(numbers)}H", *numbers)
    bits = 0
    for number in numbers:
        bits |= 1 << number
    return b"B" + bits.to_bytes((total + 7) // 8, "little")


def _encode_name(name: str, key: str) -> str:
    """Deposited spelling relative to its key, unambiguously.

    '' = the key itself, '^U' = upper case, '^C' = capitalized, '^E' = empty, '^^…' = a literal starting with ^.
    """
    if name == key:
        return ""
    if name == "":
        return "^E"
    if name == key.upper():
        return "^U"
    if name == key.capitalize():
        return "^C"
    return "^^" + name if name.startswith("^") else name


def _decode_name(stored: str, key: str) -> str:
    if stored == "":
        return key
    if stored.startswith("^^"):
        return stored[1:]
    return {"^E": "", "^U": key.upper(), "^C": key.capitalize()}.get(stored, stored)


def _decode_panels(blob: bytes) -> list[int]:
    if blob[:1] == b"L":
        return list(struct.unpack(f"<{(len(blob) - 1) // 2}H", blob[1:]))
    bits = int.from_bytes(blob[1:], "little")
    return [i for i, bit in enumerate(reversed(bin(bits)[2:])) if bit == "1"]


def write_index(panels: Sequence[Sequence[Sequence[str]]], samples: dict[str, int], target: str | Path, *,
                catalog_sha256: str = "", targeted_max: int = TARGETED_MAX) -> dict[str, Any]:
    """Write the SQLite feature index from full panels ([name, id, type] rows) and {gsm: panel}."""
    target = Path(target)
    target.unlink(missing_ok=True)
    if len(panels) >= 65536:
        raise ValueError("At most 65,535 panels are supported")
    key_panels: dict[str, set[int]] = defaultdict(set)
    key_names: dict[tuple[str, str], set[int]] = defaultdict(set)
    for number, rows in enumerate(panels):
        for name, identifier, _kind in rows:
            for key in feature_keys(name) | feature_keys(identifier):
                key_panels[key].add(number)
                key_names[(key, name)].add(number)
    sizes = [len(rows) for rows in panels]
    searchable = {k for k, found in key_panels.items()
                  if len(found) > 1 or any(sizes[p] <= targeted_max for p in found)}
    db = sqlite3.connect(target)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
        CREATE TABLE samples(gsm TEXT PRIMARY KEY, panel INTEGER NOT NULL) WITHOUT ROWID;
        CREATE TABLE panels(panel INTEGER PRIMARY KEY, n INTEGER NOT NULL, types TEXT NOT NULL, rows BLOB);
        CREATE TABLE keys(key TEXT NOT NULL, name TEXT NOT NULL, panels BLOB NOT NULL,
                          PRIMARY KEY(key, name)) WITHOUT ROWID;
    """)
    db.executemany("INSERT INTO samples VALUES (?, ?)", sorted(samples.items()))
    for number, rows in enumerate(panels):
        types = json.dumps(Counter(r[2] for r in rows), ensure_ascii=False)
        blob = (zlib.compress("\n".join("\t".join(r) for r in rows).encode(), 9)
                if len(rows) <= targeted_max else None)
        db.execute("INSERT INTO panels VALUES (?, ?, ?, ?)", (number, len(rows), types, blob))
    db.executemany("INSERT INTO keys VALUES (?, ?, ?)",
                   ((key, _encode_name(name, key), _encode_panels(found, len(panels)))
                    for (key, name), found in sorted(key_names.items()) if key in searchable))
    stats = {"schema": SCHEMA, "catalog_sha256": catalog_sha256, "samples": len(samples), "panels": len(panels),
             "targeted_max": targeted_max, "keys": len(key_panels), "searchable_keys": len(searchable),
             "feature_rows": sum(sizes)}
    db.executemany("INSERT INTO meta VALUES (?, ?)", ((k, json.dumps(v)) for k, v in stats.items()))
    db.commit()
    db.execute("VACUUM")
    db.close()
    return stats


class FeatureIndex:
    """Read-only access to a SQLite feature index."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        meta = {k: json.loads(v) for k, v in self._db.execute("SELECT key, value FROM meta")}
        if meta.get("schema") != SCHEMA:
            raise IntegrityError("Unsupported feature index schema")
        self.meta = meta
        self.catalog_sha256: str = meta.get("catalog_sha256", "")
        self.samples: dict[str, int] = dict(self._db.execute("SELECT gsm, panel FROM samples"))
        self._cache: dict[str, dict[int, str]] = {}

    @classmethod
    def from_path(cls, path: str | Path) -> FeatureIndex:
        """Open a .sqlite index, or a .sqlite.gz (decompressed once next to it)."""
        path = Path(path).expanduser()
        if path.suffix == ".gz":
            plain = path.with_suffix("")
            if not plain.exists() or plain.stat().st_mtime < path.stat().st_mtime:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle, gzip.open(path) as source:
                    shutil.copyfileobj(source, handle, length=8 << 20)
                Path(handle.name).replace(plain)
            path = plain
        return cls(path)

    def panels_with(self, feature: str) -> dict[int, str]:
        """{panel: feature name as deposited} for one query (symbol or ID, any case)."""
        key = feature_key(feature)
        if key not in self._cache:
            found: dict[int, str] = {}
            for stored, blob in self._db.execute("SELECT name, panels FROM keys WHERE key = ? ORDER BY name", (key,)):
                name = _decode_name(stored, key)
                for panel in _decode_panels(blob):
                    current = found.get(panel)
                    # Prefer the exact spelling the user typed, then the shortest deposited name.
                    if current is None or (name == feature and current != feature) or (
                            current != feature and len(name) < len(current)):
                        found[panel] = name
            self._cache[key] = found
        return self._cache[key]

    def match(self, features: Iterable[str], gsms: Iterable[str], *, mode: str = "all"
              ) -> tuple[dict[str, list[str]], dict[str, int]]:
        """Samples (of `gsms`) measuring all/any of `features`, with the matched names per sample."""
        if mode not in {"all", "any"}:
            raise ValueError("gene_match must be 'all' or 'any'")
        wanted = list(dict.fromkeys(str(f).strip() for f in features if str(f).strip()))
        hits = {f: self.panels_with(f) for f in wanted}
        counts = {f: 0 for f in wanted}
        result: dict[str, list[str]] = {}
        for gsm in gsms:
            panel = self.samples.get(gsm)
            if panel is None:
                continue
            matched = [hits[f][panel] for f in wanted if panel in hits[f]]
            for f in wanted:
                counts[f] += panel in hits[f]
            if matched and (mode == "any" or len(matched) == len(wanted)):
                result[gsm] = matched
        return result, counts

    def panel_info(self, gsm: str) -> dict[str, Any]:
        """Panel number, feature count, counts by feature type, and whether the full list is stored."""
        panel = self.samples.get(gsm)
        if panel is None:
            raise NotFoundError(f"{gsm} is not in the feature index")
        n, types, listed = self._db.execute("SELECT n, types, rows IS NOT NULL FROM panels WHERE panel = ?",
                                            (panel,)).fetchone()
        return {"panel": panel, "features": n, "feature_types": json.loads(types), "listed": bool(listed)}

    def features(self, gsm: str) -> list[dict[str, str]]:
        """Every feature of a targeted panel; whole-transcriptome panels raise NotFoundError with counts."""
        info = self.panel_info(gsm)
        blob = self._db.execute("SELECT rows FROM panels WHERE panel = ?", (info["panel"],)).fetchone()[0]
        if blob is None:
            raise NotFoundError(f"{gsm} measures {info['features']:,} features (a whole-transcriptome list); the index "
                                "stores full lists only for targeted panels",
                                hint="Use --gene to test specific genes, or download the sample and read adata.var.")
        rows = zlib.decompress(blob).decode().split("\n")
        return [dict(zip(("feature_name", "source_feature_id", "feature_type"), r.split("\t"))) for r in rows]

    def suggest(self, feature: str, n: int = 5) -> list[str]:
        """Searchable names starting with the query, for typos and partial symbols."""
        key = feature_key(feature)
        rows = self._db.execute("SELECT key, name FROM keys WHERE key > ? AND key < ? LIMIT 200",
                                (key, key + "\U0010ffff")).fetchall()
        return sorted({_decode_name(stored, k) for k, stored in rows}, key=lambda v: (len(v), v))[:n]


def load_feature_index(catalog: Any, path: str | Path | None = None) -> FeatureIndex:
    """The release's feature index: explicit path, $SDA_DB_FEATURE_INDEX, or the file the release records."""
    chosen = path or os.environ.get("SDA_DB_FEATURE_INDEX")
    if chosen:
        index = FeatureIndex.from_path(chosen)
    else:
        record = (catalog.release.feature_index if catalog.release is not None else None) or {}
        borrowed = False
        if not record and catalog.release is not None and catalog.release.rolling:
            # A rolling release reuses the paper release's index; newer samples are reported as not indexed.
            from .releases import default_release

            record, borrowed = default_release().feature_index or {}, True
        if not record:
            raise NotFoundError(f"Release {catalog.release_name} has no published feature index",
                                hint="Pass a local index (feature_index=PATH or SDA_DB_FEATURE_INDEX), "
                                     "or use the paper release.")
        from huggingface_hub import hf_hub_download

        from .catalog import sha256_file

        with hub_errors():
            local = hf_hub_download(record["repo_id"], record["path"], repo_type="dataset",
                                    revision=record["revision"], local_files_only=catalog.local_files_only,
                                    cache_dir=catalog.cache_dir)
        if sha256_file(Path(local)) != record["sha256"]:
            raise IntegrityError("The downloaded feature index differs from the release record")
        cache = Path(os.environ.get("SDA_DB_CACHE") or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                     / "sda-db") / "feature-index"
        cache.mkdir(parents=True, exist_ok=True)
        plain = cache / f"{record['sha256']}.sqlite"
        if not plain.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle, gzip.open(local) as source:
                shutil.copyfileobj(source, handle, length=8 << 20)
            Path(handle.name).replace(plain)
        index = FeatureIndex(plain)
        if borrowed:
            return index
    if catalog.catalog_sha256 and index.catalog_sha256 and index.catalog_sha256 != catalog.catalog_sha256:
        raise IntegrityError("The feature index was built for a different catalog",
                             hint="Use the index of this release, or open the release the index was built for.")
    return index
