"""Download planning (sizes before bytes move) and parallel, verified cohort downloads."""

from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import shutil
import sys
from threading import Event
import time
from typing import Any, Iterable

from .catalog import Catalog
from .download import download_sample
from .errors import ACCESS_HINT, AccessError, SdaDbError, SizeLimitError, hub_errors, translate

LOGGER = logging.getLogger(__name__)
MANIFEST_FILE = "provenance/file-manifest.jsonl"
UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    number = float(value)
    for unit in UNITS:
        if abs(number) < 1000 or unit == UNITS[-1]:
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1000
    return f"{number:.1f} PB"  # pragma: no cover


def parse_size(text: str | int | None) -> int | None:
    """Parse '50GB', '1.5 TB', '800M' (decimal), '2GiB' (binary) or a byte count."""
    if text is None or isinstance(text, int):
        return text
    cleaned = str(text).strip().upper().replace(" ", "")
    binary = cleaned.endswith(("IB", "I"))
    cleaned = cleaned.rstrip("B").rstrip("I")
    factors = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5}
    try:
        if cleaned and cleaned[-1] in factors:
            value = float(cleaned[:-1]) * (1024 if binary else 1000) ** factors[cleaned[-1]]
        else:
            value = float(cleaned)
        if not 0 <= value < 1e21:
            raise ValueError
        return int(value)
    except (ValueError, OverflowError):
        raise ValueError(f"Cannot parse size {text!r}; use e.g. 500MB, 20GB, 1.5TB or 2GiB") from None


def _assets(row: dict[str, Any], include_image: bool) -> list[str]:
    names = ["sample.json", "spatial.h5ad"]
    if include_image and row["has_image"]:
        names.append("image.tif")
    return names


@dataclass
class SamplePlan:
    gsm: str
    sample_dir: str
    files: dict[str, int | None]
    local_files: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int | None:
        values = list(self.files.values())
        return None if any(v is None for v in values) else int(sum(values))  # type: ignore[arg-type]

    @property
    def fetch_bytes(self) -> int | None:
        values = [v for k, v in self.files.items() if k not in self.local_files]
        return None if any(v is None for v in values) else int(sum(values))  # type: ignore[arg-type]


@dataclass
class DownloadPlan:
    """What a download would fetch, before any sample bytes move."""

    release: str
    revision: str
    destination: str
    include_image: bool
    samples: list[SamplePlan]
    size_source: str
    notes: list[str] = field(default_factory=list)
    free_bytes: int | None = None
    access_denied: bool = False

    @property
    def total_bytes(self) -> int | None:
        values = [s.bytes for s in self.samples]
        return None if any(v is None for v in values) else int(sum(values))  # type: ignore[arg-type]

    @property
    def fetch_bytes(self) -> int | None:
        values = [s.fetch_bytes for s in self.samples]
        return None if any(v is None for v in values) else int(sum(values))  # type: ignore[arg-type]

    @property
    def unknown(self) -> int:
        return sum(1 for s in self.samples if s.bytes is None)

    def to_dict(self, *, per_sample: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "release": self.release, "commit": self.revision, "destination": self.destination,
            "include_image": self.include_image, "samples": len(self.samples),
            "total_bytes": self.total_bytes, "total": human_bytes(self.total_bytes),
            "to_fetch_bytes": self.fetch_bytes, "to_fetch": human_bytes(self.fetch_bytes),
            "already_local": sum(1 for s in self.samples if s.local_files and s.fetch_bytes == 0),
            "unknown_sizes": self.unknown, "free_bytes": self.free_bytes, "size_source": self.size_source,
            "access_denied": self.access_denied,
            "notes": self.notes}
        if per_sample:
            result["per_sample"] = [{"gsm": s.gsm, "bytes": s.bytes, "files": s.files,
                                     "local_files": s.local_files} for s in self.samples]
        return result


def _manifest_sizes(catalog: Catalog) -> dict[str, tuple[int, str]]:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    with hub_errors():
        try:
            path = hf_hub_download(catalog.repo_id, MANIFEST_FILE, repo_type="dataset", revision=catalog.revision,
                                   local_files_only=catalog.local_files_only, cache_dir=catalog.cache_dir)
        except EntryNotFoundError:
            return {}
    sizes = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                sizes[record["path"]] = (int(record["bytes"]), str(record.get("sha256", "")))
    return sizes


def _paths_info(catalog: Catalog, paths: list[str]) -> dict[str, int]:
    from huggingface_hub import HfApi

    api, found = HfApi(), {}
    for start in range(0, len(paths), 200):
        with hub_errors():
            for info in api.get_paths_info(catalog.repo_id, paths[start:start + 200], repo_type="dataset",
                                           revision=catalog.revision):
                size = getattr(info, "size", None)
                if isinstance(size, int):
                    found[info.path] = size
    return found


def plan_download(catalog: Catalog, *, destination: str | Path = "downloads", include_image: bool = False,
                  gsms: Iterable[str] | None = None) -> DownloadPlan:
    """Sizes from the release file manifest (or Hub path metadata), plus local reuse and free space.

    Sizes need approved dataset access. Without it the plan lists files with unknown sizes.
    """
    selection = catalog.select(gsms) if gsms is not None else catalog
    root = Path(destination).expanduser().resolve()
    notes: list[str] = []
    source = "release file manifest"
    sizes: dict[str, int] = {}
    denied = False
    try:
        manifest = _manifest_sizes(selection)
        for row in selection.rows:
            for name, column in (("spatial.h5ad", "expression_sha256"), ("image.tif", "image_sha256")):
                entry = manifest.get(f"{row['sample_dir']}/{name}")
                if entry and row.get(column) and entry[1] and entry[1] != row[column]:
                    notes.append(f"{row['gsm']}/{name}: manifest SHA256 differs from the catalog")
        sizes = {path: entry[0] for path, entry in manifest.items()}
        wanted = [f"{row['sample_dir']}/{name}" for row in selection.rows
                  for name in _assets(row, include_image)]
        missing = [path for path in wanted if path not in sizes]
        if missing and not selection.local_files_only:
            sizes.update(_paths_info(selection, missing))
            source = "release file manifest + Hub path metadata"
    except AccessError:
        source, denied = "unavailable", True
        notes.append("Sizes need approved dataset access. " + ACCESS_HINT)
    except SdaDbError as exc:
        source = "unavailable"
        notes.append(f"Sizes unavailable: {exc}")
    samples = []
    for row in selection.rows:
        files = {name: sizes.get(f"{row['sample_dir']}/{name}") for name in _assets(row, include_image)}
        directory = root / selection.revision / row["sample_dir"]
        local = [name for name, size in files.items()
                 if (directory / name).is_file() and (size is None or (directory / name).stat().st_size == size)]
        samples.append(SamplePlan(row["gsm"], row["sample_dir"], files, local))
    if include_image and (absent := sum(1 for r in selection.rows if not r["has_image"])):
        notes.append(f"{absent} selected samples have no released image; only expression is planned for them.")
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free if probe.exists() else None
    plan = DownloadPlan(selection.release_name, selection.revision, str(root), include_image, samples,
                        source, notes, free, denied)
    if free is not None and plan.fetch_bytes is not None and plan.fetch_bytes > free:
        notes.append(f"Needs {human_bytes(plan.fetch_bytes)} but only {human_bytes(free)} is free at {probe}.")
    return plan


@dataclass
class DownloadReport:
    release: str
    revision: str
    destination: str
    completed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def directories(self) -> list[Path]:
        return [Path(item["directory"]) for item in self.completed]

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok, "summary": f"{len(self.completed)} downloaded, {len(self.failed)} failed, "
                                                          f"{len(self.cancelled)} not started"}

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def download(catalog: Catalog, destination: str | Path = "downloads", *, gsms: Iterable[str] | None = None,
             include_image: bool = False, workers: int = 4, max_bytes: int | str | None = None,
             storage: str = "hub", on_error: str = "raise", progress: bool | None = None) -> DownloadReport:
    """Download, verify and receipt every selected sample, `workers` samples at a time.

    `max_bytes` (e.g. '50GB') refuses the whole download before any transfer when the
    planned bytes still to fetch exceed it. on_error='continue' records failures and
    keeps going; 'raise' stops scheduling new samples and re-raises the first error.
    """
    if on_error not in {"raise", "continue"}:
        raise ValueError("on_error must be 'raise' or 'continue'")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("workers must be an integer from 1 to 16")
    selection = catalog.select(gsms) if gsms is not None else catalog
    limit = parse_size(max_bytes)
    if limit is not None:
        plan = plan_download(selection, destination=destination, include_image=include_image)
        if plan.access_denied:
            raise AccessError("Sizes and files need approved dataset access", hint=ACCESS_HINT)
        if plan.fetch_bytes is None:
            raise SizeLimitError("Cannot enforce the size limit: some file sizes are unknown",
                                 hint="Check access with 'sda-db doctor', or drop the size limit deliberately.")
        if plan.fetch_bytes > limit:
            raise SizeLimitError(f"Download needs {human_bytes(plan.fetch_bytes)} for {len(plan.samples)} samples, "
                                 f"above the limit of {human_bytes(limit)}",
                                 hint="Narrow the selection, omit images, raise --max-size, or stream with a disk budget.")
    root = Path(destination).expanduser()
    report = DownloadReport(selection.release_name, selection.revision, str(root.resolve()))
    show = sys.stderr.isatty() if progress is None else progress
    bar = None
    from huggingface_hub.utils import are_progress_bars_disabled, disable_progress_bars, enable_progress_bars

    restore = not are_progress_bars_disabled()
    if workers > 1 or not show:
        disable_progress_bars()  # per-file bars from parallel threads would interleave
    if show:
        from tqdm.auto import tqdm

        bar = tqdm(total=len(selection.rows), unit="sample", desc="sda-db download", file=sys.stderr)
    started = time.monotonic()
    cancel = Event()
    first_error: BaseException | None = None
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sda-db-download")
    try:
        futures = {pool.submit(download_sample, selection, row["gsm"], root, include_image=include_image,
                               storage=storage, _cancel=cancel): row["gsm"] for row in selection.rows}
        for future in as_completed(futures):
            gsm = futures[future]
            try:
                directory = future.result()
                report.completed.append({"gsm": gsm, "directory": str(directory)})
            except CancelledError:
                report.cancelled.append(gsm)
            except Exception as exc:  # noqa: BLE001 - reported per sample
                error = translate(exc) or exc
                detail = error.to_dict() if isinstance(error, SdaDbError) else {
                    "code": type(exc).__name__, "message": str(exc)}
                report.failed.append({"gsm": gsm, "error": detail})
                if on_error == "continue":
                    LOGGER.warning("%s failed: %s", gsm, detail["message"])
                if on_error == "raise" and first_error is None:
                    first_error = error
                    cancel.set()
                    for other in futures:
                        if other.cancel():
                            report.cancelled.append(futures[other])
            if bar is not None:
                bar.update(1)
    except BaseException:
        # Ctrl-C or an unexpected error: drop queued samples first, then tell active ones to stop at their
        # next check, so no queued sample can start in between.
        pool.shutdown(wait=False, cancel_futures=True)
        cancel.set()
        pool.shutdown(wait=True)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if bar is not None:
            bar.close()
        if restore:
            enable_progress_bars()
    order = {row["gsm"]: i for i, row in enumerate(selection.rows)}
    report.completed.sort(key=lambda item: order[item["gsm"]])
    report.cancelled = sorted(set(report.cancelled), key=order.__getitem__)
    report.elapsed_seconds = round(time.monotonic() - started, 2)
    if first_error is not None:
        first_error.report = report  # type: ignore[attr-defined]
        raise first_error
    return report
