"""Sample-at-a-time access with bounded prefetch and an exclusively managed disk cache."""

from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from threading import Condition, Event
import time
from typing import Any, Iterable, Iterator

from filelock import FileLock

from .catalog import COMMIT, Catalog
from .download import _check_row, _read_metadata, download_sample, verify_sample

DEFAULT_CACHE_BYTES = 10 * 1024**3
MARKER = ".sda-db-cache.json"
ENTRY_RECORD = "entry.json"
ASSETS = frozenset({"sample.json", "spatial.h5ad", "image.tif"})


class CacheLimitError(ValueError):
    """A single requested sample cannot fit within the configured payload budget."""


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _entry_key(repo_id: str, revision: str, gsm: str) -> str:
    return hashlib.sha256(json.dumps([repo_id, revision, gsm]).encode()).hexdigest()


@dataclass
class StreamSample:
    """A verified sample lease, valid until release or the next iterator advance.

    Close backed AnnData and TIFF handles before advancing. Use download_sample
    for permanent files; a stream cache may evict a released sample immediately.
    """

    gsm: str
    row: dict[str, Any]
    _directory: Path
    _key: str
    _stream: SampleStream
    _released: bool = False

    @property
    def directory(self) -> Path:
        if self._released:
            raise RuntimeError("Sample lease released; download it permanently to keep using its files")
        return self._directory

    def release(self) -> None:
        """Allow cache eviction after the caller has closed all open file handles."""
        if not self._released:
            self._released = True
            self._stream._release(self._key)

    def __enter__(self) -> StreamSample:
        self.directory
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


class SampleStream(Iterator[StreamSample]):
    """Context-managed, ordered iteration over verified samples.

    One background download worker overlaps transfer with consumer computation.
    prefetch bounds queued/ready samples ahead of the consumer, not HTTP threads.
    Payload sizes are reserved before download; leased and prefetched samples
    cannot be evicted. If the next sample needs more room, prefetch waits for the
    consumer to advance. A cache directory has one exclusive stream owner.
    """

    def __init__(self, catalog: Catalog, gsms: Iterable[str] | None = None, *,
                 cache_dir: str | Path = ".sda-cache", max_cache_bytes: int = DEFAULT_CACHE_BYTES,
                 prefetch: int = 1, include_image: bool = False) -> None:
        if type(max_cache_bytes) is not int or max_cache_bytes <= 0:
            raise ValueError("max_cache_bytes must be a positive integer")
        if type(prefetch) is not int or not 0 <= prefetch <= 4:
            raise ValueError("prefetch must be an integer from 0 to 4")
        if isinstance(gsms, str):
            raise ValueError("gsms must be an iterable of GSM identifiers, not a string")
        identifiers = list(dict.fromkeys(gsms if gsms is not None else (r["gsm"] for r in catalog.rows)))
        # Freeze selection/identity before any disk or network work, including typo checks.
        self.catalog = Catalog([catalog.get(gsm) for gsm in identifiers], catalog.repo_id, catalog.revision,
                               catalog.catalog_sha256, catalog.local_files_only, catalog.cache_dir)
        self._identifiers = identifiers
        self.root = Path(cache_dir).expanduser().resolve()
        self.max_cache_bytes = max_cache_bytes
        self.prefetch = prefetch
        self.include_image = include_image
        self._condition = Condition()
        self._stop = Event()
        self._entries: dict[str, dict[str, Any]] = {}
        self._pins: set[str] = set()
        self._futures: deque[Future[StreamSample]] = deque()
        self._cursor = 0
        self._current: StreamSample | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = FileLock(str(self.root / ".lock"))
        self._opened = False
        self._closed = False

    def __enter__(self) -> SampleStream:
        if self._opened or self._closed:
            raise RuntimeError("A sample stream can be entered only once")
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        if marker.is_symlink():
            raise ValueError("Cache marker cannot be a symlink")
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError("Cache directory is nonempty and unmanaged; choose a dedicated empty directory")
            _write_json(marker, {"schema": "sda-db-cache-1"})
        if json.loads(marker.read_text(encoding="utf-8")) != {"schema": "sda-db-cache-1"}:
            raise ValueError("Unsupported managed-cache schema")
        self._lock.acquire(timeout=0)
        try:
            self._load_entries()
            self._opened = True
            if self.prefetch:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sda-db-prefetch")
        except BaseException:
            self._lock.release()
            raise
        return self

    def _entry_path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid managed-cache key")
        path = self.root / "entries" / key
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("Cache entry escapes the managed directory")
        return path

    def _load_entries(self) -> None:
        entries = self.root / "entries"
        if entries.is_symlink():
            raise ValueError("Cache entries directory cannot be a symlink")
        entries.mkdir(exist_ok=True)
        for path in entries.iterdir():
            self._entry_path(path.name)
            record_path = path / ENTRY_RECORD
            if not record_path.exists() and path.is_dir() and not any(path.iterdir()):
                path.rmdir()  # Recover interruption between mkdir and entry-record creation.
                continue
            if record_path.is_symlink():
                raise ValueError("Cache entry record cannot be a symlink")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if (record.get("schema") != "sda-db-cache-entry-1" or not isinstance(record.get("repo_id"), str)
                    or not COMMIT.fullmatch(str(record.get("revision", "")))
                    or not re.fullmatch(r"GSM[0-9]+", str(record.get("gsm", "")))
                    or _entry_key(record["repo_id"], record["revision"], record["gsm"]) != path.name
                    or type(record.get("bytes")) is not int or record["bytes"] <= 0
                    or type(record.get("last_used")) is not int
                    or not isinstance(record.get("file_sizes"), dict)
                    or not {"sample.json", "spatial.h5ad"} <= record["file_sizes"].keys() <= ASSETS
                    or any(type(size) is not int or size <= 0 for size in record["file_sizes"].values())
                    or sum(record["file_sizes"].values()) != record["bytes"]
                    or record.get("state") not in {"ready", "partial"}):
                raise ValueError("Invalid managed-cache entry record")
            if any(p.is_symlink() for p in path.rglob("*")):
                raise ValueError("Symlinks are not allowed inside the managed cache")
            if record["state"] == "partial":
                shutil.rmtree(path)  # Only this client's disposable, unfinished cache entry.
                continue
            actual = sum(p.stat().st_size for p in (path / "payload").rglob("*")
                         if p.is_file() and p.name in ASSETS)
            if actual != record["bytes"]:
                raise ValueError("Managed-cache payload sizes differ from their entry record")
            record["bytes"] = actual
            self._entries[path.name] = record
        while self._allocated() > self.max_cache_bytes:
            self._evict_one()

    def _allocated(self) -> int:
        return sum(entry["bytes"] for entry in self._entries.values())

    def _evict_one(self, *, exclude: str | None = None) -> bool:
        candidates = [(entry.get("last_used", 0), key) for key, entry in self._entries.items()
                      if key not in self._pins and key != exclude]
        if not candidates:
            return False
        _, key = min(candidates)
        shutil.rmtree(self._entry_path(key))
        del self._entries[key]
        return True

    def _plan(self, row: dict[str, Any]) -> dict[str, int]:
        from huggingface_hub import get_hf_file_metadata, hf_hub_url

        if self.catalog.local_files_only:
            raise FileNotFoundError(f"{row['gsm']} is not complete in this stream cache; offline mode cannot fetch it")
        names = ["sample.json", "spatial.h5ad"]
        if self.include_image and row["has_image"]:
            names.append("image.tif")
        sizes = {}
        for name in names:
            if self._stop.is_set():
                raise CancelledError("Sample stream closed")
            url = hf_hub_url(self.catalog.repo_id, f"{row['sample_dir']}/{name}", repo_type="dataset",
                             revision=self.catalog.revision)
            info = get_hf_file_metadata(url)
            if info.commit_hash != self.catalog.revision or type(info.size) is not int or info.size <= 0:
                raise ValueError(f"Cannot reserve {row['gsm']}/{name}: invalid size or commit")
            sizes[name] = info.size
        return sizes

    def _prepare(self, gsm: str) -> StreamSample:
        if self._stop.is_set():
            raise CancelledError("Sample stream closed")
        row = self.catalog.get(gsm)
        key = _entry_key(self.catalog.repo_id, self.catalog.revision, gsm)
        entry_path = self._entry_path(key)
        directory = entry_path / "payload" / self.catalog.revision / row["sample_dir"]
        with self._condition:
            existing = self._entries.get(key)
            reusable = (existing is not None and existing["state"] == "ready"
                        and (not self.include_image or not row["has_image"] or "image.tif" in existing["file_sizes"]))
            if reusable:
                self._pins.add(key)
        if reusable:
            try:
                verify_sample(directory, require_image=self.include_image and row["has_image"])
                _check_row(_read_metadata(directory / "sample.json"), row)
                with self._condition:
                    existing["last_used"] = time.time_ns()
                    _write_json(entry_path / ENTRY_RECORD, existing)
                return StreamSample(gsm, row, directory, key, self)
            except BaseException:
                self._release(key)
                raise

        sizes = self._plan(row)
        needed = sum(sizes.values())
        if needed > self.max_cache_bytes:
            raise CacheLimitError(f"{gsm} needs {needed} payload bytes; max_cache_bytes={self.max_cache_bytes}. "
                                  "Increase the limit, omit images, or download this sample permanently.")
        with self._condition:
            while True:
                if self._stop.is_set():
                    raise CancelledError("Sample stream closed")
                previous_size = self._entries.get(key, {}).get("bytes", 0)
                if self._allocated() - previous_size + needed <= self.max_cache_bytes:
                    break
                if not self._evict_one(exclude=key):
                    self._condition.wait()
            record = {"schema": "sda-db-cache-entry-1", "repo_id": self.catalog.repo_id,
                      "revision": self.catalog.revision, "gsm": gsm, "bytes": needed,
                      "file_sizes": sizes, "last_used": time.time_ns(), "state": "partial"}
            _write_json(entry_path / ENTRY_RECORD, record)
            self._entries[key] = record
            self._pins.add(key)
        try:
            directory = download_sample(self.catalog, gsm, entry_path / "payload", include_image=self.include_image,
                                        storage="direct", _cancel=self._stop)
            if any((directory / name).stat().st_size != size for name, size in sizes.items()):
                raise ValueError(f"{gsm} file sizes changed after the fixed-commit reservation")
            with self._condition:
                record["state"] = "ready"
                record["last_used"] = time.time_ns()
                _write_json(entry_path / ENTRY_RECORD, record)
            return StreamSample(gsm, row, directory, key, self)
        except BaseException:
            with self._condition:
                self._pins.discard(key)
                shutil.rmtree(entry_path)
                self._entries.pop(key, None)
                self._condition.notify_all()
            raise

    def _release(self, key: str) -> None:
        with self._condition:
            self._pins.discard(key)
            self._condition.notify_all()

    @property
    def cache_info(self) -> dict[str, int]:
        """Report payload allocations, including full reservations for active transfers.

        The limit covers sample.json, expression and requested images. Small Hub
        bookkeeping files, receipts and filesystem allocation overhead are separate.
        """
        with self._condition:
            return {"max_bytes": self.max_cache_bytes, "allocated_bytes": self._allocated(),
                    "ready_bytes": sum(e["bytes"] for e in self._entries.values() if e["state"] == "ready"),
                    "reserved_bytes": sum(e["bytes"] for e in self._entries.values() if e["state"] == "partial"),
                    "cached_samples": sum(e["state"] == "ready" for e in self._entries.values()),
                    "pinned_samples": len(self._pins)}

    def __iter__(self) -> SampleStream:
        return self

    def __next__(self) -> StreamSample:
        if not self._opened:
            raise RuntimeError("Use 'with stream_samples(...) as samples' before iterating")
        if self._closed:
            raise StopIteration
        if self._current is not None:
            self._current.release()
            self._current = None
        try:
            if self._executor is None:
                if self._cursor >= len(self._identifiers):
                    self.close()
                    raise StopIteration
                gsm = self._identifiers[self._cursor]
                self._cursor += 1
                sample = self._prepare(gsm)
            else:
                while len(self._futures) < self.prefetch + 1 and self._cursor < len(self._identifiers):
                    gsm = self._identifiers[self._cursor]
                    self._cursor += 1
                    self._futures.append(self._executor.submit(self._prepare, gsm))
                if not self._futures:
                    self.close()
                    raise StopIteration
                sample = self._futures.popleft().result()
            self._current = sample
            return sample
        except StopIteration:
            raise
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Cancel queued work and release leases after an active Hub file finishes.

        The SDK does not forcibly interrupt Hub's network worker. Wait for any
        active file transfer before releasing the exclusive cache lock.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        try:
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
            if self._current is not None:
                self._current.release()
                self._current = None
            with self._condition:
                self._pins.clear()
        finally:
            if self._opened:
                self._lock.release()

    def __exit__(self, *args: Any) -> None:
        self.close()


def stream_samples(catalog: Catalog, gsms: Iterable[str] | None = None, *,
                   cache_dir: str | Path = ".sda-cache", max_cache_bytes: int = DEFAULT_CACHE_BYTES,
                   prefetch: int = 1, include_image: bool = False) -> SampleStream:
    """Create an ordered sample stream; acquire resources only inside its context."""
    return SampleStream(catalog, gsms, cache_dir=cache_dir, max_cache_bytes=max_cache_bytes,
                        prefetch=prefetch, include_image=include_image)
