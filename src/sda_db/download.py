"""Selective downloads with release-bound metadata and streaming SHA256 checks."""

from __future__ import annotations

from concurrent.futures import CancelledError
import json
import logging
from pathlib import Path
import shutil
import tempfile
from threading import Event
from typing import Any

from .catalog import Catalog, SHA256, sha256_file

LOGGER = logging.getLogger(__name__)


def _read_metadata(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "sda-release-sample-1":
        raise ValueError("Unsupported sample.json schema")
    for key, filename in (("expression", "spatial.h5ad"), ("image", "image.tif")):
        asset = value.get(key)
        if key == "image" and asset is None:
            continue
        if (not isinstance(asset, dict) or asset.get("file") != filename
                or not SHA256.fullmatch(str(asset.get("sha256", "")))):
            raise ValueError(f"Invalid {key} asset metadata")
    return value


def _check_row(meta: dict[str, Any], row: dict[str, Any]) -> None:
    comparisons = [(meta.get("gsm"), row["gsm"]), (meta.get("published_gse"), row["published_gse"]),
                   (meta.get("gse"), row["gse"].split("+")), (meta.get("split"), row["split"]),
                   (meta["expression"].get("sha256"), row["expression_sha256"]),
                   (meta["expression"].get("n_obs"), row["n_obs"]),
                   (meta["expression"].get("n_vars"), row["n_vars"]),
                   (meta["expression"].get("state"), row["expression_state"]),
                   (meta.get("coordinates", {}).get("units"), row["coordinate_units"]),
                   (meta.get("image") is not None, row["has_image"])]
    if row["has_image"]:
        comparisons.append(((meta.get("image") or {}).get("sha256"), row["image_sha256"]))
    if any(actual != expected for actual, expected in comparisons):
        raise ValueError(f"sample.json disagrees with catalog for {row['gsm']}")


def _safe_target(root: Path, relative: str) -> Path:
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Download target escapes destination (including symlinks)")
    if target.is_symlink():
        raise ValueError(f"Refusing a symlink download target: {target.name}")
    return target


def _install(source: Path, target: Path, expected: str, *, move: bool = False) -> None:
    """Preserve existing user files; install only verified bytes through a temporary file."""
    if target.exists():
        if sha256_file(target) != expected:
            raise ValueError(f"Existing file has different bytes: {target}; use a new destination")
        if move and source != target:
            source.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if move:
        if sha256_file(source) != expected:
            raise ValueError(f"SHA256 mismatch: {target.name}")
        try:
            target.hardlink_to(source)
        except FileExistsError:
            if sha256_file(target) != expected:
                raise ValueError(f"Concurrent conflicting file: {target}")
        source.unlink()
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".sda-", delete=False) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, handle, length=4 * 1024 * 1024)
        if sha256_file(temporary) != expected:
            raise ValueError(f"SHA256 mismatch: {target.name}")
        # link() creates the name only if absent, preserving concurrent user writes.
        try:
            target.hardlink_to(temporary)
        except FileExistsError:
            if sha256_file(target) != expected:
                raise ValueError(f"Concurrent conflicting file: {target}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_sample(catalog: Catalog, gsm: str, destination: str | Path = "downloads", *,
                    include_image: bool = False, storage: str = "hub",
                    _cancel: Event | None = None) -> Path:
    """Download one sample to destination/<commit>/samples/<GSE>/<GSM>.

    Images are opt-in. storage='direct' stages Hub transfers beside their final
    files and installs verified bytes without a second full-size copy. The
    default 'hub' preserves shared Hub caching and offline reuse.
    """
    from huggingface_hub import hf_hub_download

    if storage not in {"hub", "direct"}:
        raise ValueError("storage must be 'hub' or 'direct'")
    row = catalog.get(gsm)
    root = Path(destination).expanduser().resolve()
    relative = f"{catalog.revision}/{row['sample_dir']}"
    directory = _safe_target(root, relative)

    def fetch(filename: str) -> Path:
        if _cancel is not None and _cancel.is_set():
            raise CancelledError("Sample stream closed")
        options: dict[str, Any] = {"cache_dir": catalog.cache_dir,
                                   "local_files_only": catalog.local_files_only}
        if storage == "direct":
            staging = _safe_target(root, f"{catalog.revision}/.sda/staging/{gsm}")
            _safe_target(root, f"{catalog.revision}/.sda/staging/{gsm}/{row['sample_dir']}/{filename}")
            options["local_dir"] = staging
        source = Path(hf_hub_download(catalog.repo_id, f"{row['sample_dir']}/{filename}", repo_type="dataset",
                                     revision=catalog.revision, **options))
        if storage == "direct" and source.resolve() != (staging / row["sample_dir"] / filename).resolve():
            raise ValueError("Hub returned a file outside the direct download staging directory")
        if _cancel is not None and _cancel.is_set():
            raise CancelledError("Sample stream closed")
        return source

    metadata_target = _safe_target(root, f"{relative}/sample.json")
    receipt = _safe_target(root, f"{catalog.revision}/.sda/receipts/{gsm}.json")
    # A completed receipt binds local metadata to the same pinned source. Reusing
    # it avoids a network request when all requested payloads are already present.
    previous = json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else None
    reuse_metadata = (isinstance(previous, dict) and previous.get("schema") == "sda-db-download-1"
                      and previous.get("repo_id") == catalog.repo_id and previous.get("revision") == catalog.revision
                      and previous.get("gsm") == gsm and previous.get("sample_dir") == row["sample_dir"]
                      and metadata_target.is_file())
    if reuse_metadata:
        if sha256_file(metadata_target) != previous.get("files", {}).get("sample.json"):
            raise ValueError("Local sample.json differs from its recorded receipt")
        metadata_source = metadata_target
    else:
        metadata_source = fetch("sample.json")
    metadata = _read_metadata(metadata_source)
    _check_row(metadata, row)
    metadata_hash = sha256_file(metadata_source)
    _install(metadata_source, metadata_target, metadata_hash,
             move=storage == "direct" and metadata_source != metadata_target)
    files = {"sample.json": metadata_hash}
    assets = [("spatial.h5ad", row["expression_sha256"])]
    if include_image and row["has_image"]:
        assets.append(("image.tif", row["image_sha256"]))
    for filename, digest in assets:
        target = _safe_target(root, f"{relative}/{filename}")
        if target.exists():
            if sha256_file(target) != digest:
                raise ValueError(f"Existing file has different bytes: {target}; use a new destination")
            LOGGER.info("Reusing verified %s", filename)
        else:
            _install(fetch(filename), target, digest, move=storage == "direct")
        files[filename] = digest
    receipt.parent.mkdir(parents=True, exist_ok=True)
    # Preserve earlier image verification when a later call requests only expression.
    image_target = _safe_target(root, f"{relative}/image.tif")
    if row["has_image"] and image_target.exists() and sha256_file(image_target) == row["image_sha256"]:
        files["image.tif"] = row["image_sha256"]
    record = {"schema": "sda-db-download-1", "repo_id": catalog.repo_id, "revision": catalog.revision,
              "catalog_sha256": catalog.catalog_sha256, "gsm": gsm, "sample_dir": row["sample_dir"],
              "files": files}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=receipt.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(receipt)
    finally:
        temporary.unlink(missing_ok=True)
    return directory


def verify_sample(directory: str | Path, *, require_image: bool = False) -> dict[str, Any]:
    """Verify local expression and any local image against sample.json, without networking.

    If a downloader receipt exists, also verify metadata bytes and bind identity
    to that receipt. Without one, this checks local consistency, not Hub provenance.
    """
    directory = Path(directory)
    metadata_path = directory / "sample.json"
    meta = _read_metadata(metadata_path)
    files = {"sample.json": sha256_file(metadata_path)}
    for key in ("expression", "image"):
        asset = meta.get(key)
        if asset is None:
            if key == "image" and (directory / "image.tif").exists():
                raise ValueError("Local image is not declared by sample.json")
            if key == "image" and require_image:
                raise ValueError("This sample has no released image")
            continue
        path = directory / asset["file"]
        if not path.exists() and key == "image" and not require_image:
            continue
        if not path.is_file() or sha256_file(path) != asset["sha256"]:
            raise ValueError(f"Missing file or SHA256 mismatch: {path.name}")
        files[path.name] = asset["sha256"]
    receipt_path = directory.parent.parent.parent / ".sda/receipts" / f"{directory.name}.json"
    receipt = None
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (receipt.get("schema") != "sda-db-download-1" or receipt.get("gsm") != meta.get("gsm")
                or meta.get("gsm") != directory.name or meta.get("published_gse") != directory.parent.name
                or receipt.get("revision") != directory.parent.parent.parent.name
                or receipt.get("sample_dir") != f"samples/{meta.get('published_gse')}/{meta.get('gsm')}"):
            raise ValueError("Download receipt identity mismatch")
        if any(files.get(name) != digest for name, digest in receipt["files"].items()):
            raise ValueError("Downloaded files differ from their recorded receipt")
    return {"gsm": meta.get("gsm"), "files": files, "revision": receipt["revision"] if receipt else None,
            "receipt_verified": receipt is not None}
