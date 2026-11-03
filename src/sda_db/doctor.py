"""Environment, network and access diagnostics with a concrete fix for every failure."""

from __future__ import annotations

from importlib import metadata as importlib_metadata
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Any

from .errors import ACCESS_HINT, DATASET_URL, AccessError, SdaDbError, translate


def _check(name: str, status: str, detail: str, fix: str | None = None) -> dict[str, Any]:
    entry = {"check": name, "status": status, "detail": detail}
    if fix:
        entry["fix"] = fix
    return entry


def _version(package: str) -> str | None:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return None


def run(*, release: str | None = None, destination: str | Path = "downloads", network: bool = True,
        cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Run all checks; never prints or returns credentials."""
    from . import __version__
    from .releases import resolve_release

    checks = [_check("python", "ok" if sys.version_info >= (3, 11) else "fail",
                     f"Python {platform.python_version()} on {platform.system()}",
                     None if sys.version_info >= (3, 11) else "Use Python 3.11 or later."),
              _check("client", "ok", f"spatial-data-agent-db {__version__}, huggingface_hub "
                                     f"{_version('huggingface_hub')}")]
    optional = {name: _version(name) for name in ("anndata", "numpy", "tifffile", "pandas", "zarr")}
    missing = [n for n, v in optional.items() if v is None]
    checks.append(_check("analysis extras", "ok" if not missing else "warn",
                         ", ".join(f"{n} {v}" for n, v in optional.items() if v) or "none installed",
                         f"pip install 'spatial-data-agent-db[analysis]' (missing: {', '.join(missing)})"
                         if missing else None))
    try:
        rel = resolve_release(release, offline=not network)
        checks.append(_check("release", "ok", f"{rel.name} = {rel.repo_id}@{rel.revision}"
                                              + (" (public catalog mirror)" if rel.public_repo_id else "")))
    except SdaDbError as exc:
        checks.append(_check("release", "fail", str(exc), exc.hint))
        rel = None
    from huggingface_hub import get_token

    token = get_token()
    checks.append(_check("credential", "ok" if token else "warn",
                         "a Hugging Face token is configured" if token else "no Hugging Face token found",
                         None if token else "Run 'hf auth login' (or set HF_TOKEN) after your access is approved."))
    destination = Path(destination).expanduser().resolve()
    probe = destination
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    checks.append(_check("disk", "ok" if usage.free > 50e9 else "warn",
                         f"{usage.free / 1e9:.1f} GB free at {probe}",
                         None if usage.free > 50e9 else "The paper release is about 1.8 TB; plan downloads first."))
    hub_cache = cache_dir or os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")
    checks.append(_check("hub cache", "ok", f"{hub_cache or '~/.cache/huggingface (default)'}"))
    if network and rel is not None:
        from huggingface_hub import HfApi, get_hf_file_metadata, hf_hub_url

        if rel.public_repo_id:
            try:
                get_hf_file_metadata(hf_hub_url(rel.public_repo_id, "catalog/samples.csv", repo_type="dataset",
                                                revision=rel.public_revision), token=False)
                checks.append(_check("public catalog", "ok", f"{rel.public_repo_id} is reachable without login"))
            except Exception as exc:  # noqa: BLE001
                error = translate(exc)
                checks.append(_check("public catalog", "fail", str(error or type(exc).__name__),
                                     getattr(error, "hint", None)))
        if token:
            try:
                user = HfApi().whoami()
                checks.append(_check("login", "ok", f"logged in as {user.get('name', '?')}"))
            except Exception as exc:  # noqa: BLE001
                error = translate(exc)
                checks.append(_check("login", "fail", str(error or type(exc).__name__),
                                     "Your token is invalid or expired; run 'hf auth login' again."))
        try:
            get_hf_file_metadata(hf_hub_url(rel.repo_id, "catalog/samples.csv", repo_type="dataset",
                                            revision=rel.revision))
            checks.append(_check("dataset access", "ok", f"approved: files of {rel.repo_id} can be downloaded"))
        except Exception as exc:  # noqa: BLE001
            error = translate(exc)
            if isinstance(error, AccessError):
                checks.append(_check("dataset access", "fail", "no approved access to sample files",
                                     ACCESS_HINT))
            else:
                checks.append(_check("dataset access", "fail", str(error or type(exc).__name__),
                                     getattr(error, "hint", None)))
    status = "fail" if any(c["status"] == "fail" for c in checks) else (
        "warn" if any(c["status"] == "warn" for c in checks) else "ok")
    return {"status": status, "dataset": DATASET_URL, "checks": checks}
