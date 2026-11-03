"""Error types with stable codes and actionable hints, for people and agents alike."""

from __future__ import annotations

from contextlib import contextmanager
import errno
from typing import Iterator

DATASET_URL = "https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB"
ACCESS_HINT = (f"Request access at {DATASET_URL} (manual review, institutional e-mail), then run "
               "'hf auth login' or set HF_TOKEN. Searching the paper release works without approval.")


class SdaDbError(Exception):
    """Base class: `code` is stable for scripts and agents, `hint` says what to do next."""

    code = "error"
    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        return result


class AccessError(SdaDbError, PermissionError):
    """Dataset access is missing, not yet approved, or the credential is invalid."""

    code = "access_denied"
    exit_code = 4


class NotFoundError(SdaDbError, ValueError):
    """An identifier, release or cached file does not exist."""

    code = "not_found"
    exit_code = 3


class UnknownFieldError(SdaDbError, ValueError):
    """A filter names a catalog field that does not exist."""

    code = "unknown_field"
    exit_code = 2


class UnknownValueError(SdaDbError, ValueError):
    """A controlled-vocabulary filter value matches nothing in this release."""

    code = "unknown_value"
    exit_code = 2


class IntegrityError(SdaDbError, ValueError):
    """Bytes or metadata disagree with the pinned release."""

    code = "integrity"
    exit_code = 5


class NetworkError(SdaDbError, ConnectionError):
    """The Hub could not be reached."""

    code = "network"
    exit_code = 6


class SizeLimitError(SdaDbError, ValueError):
    """A planned download exceeds the caller's size limit."""

    code = "size_limit"
    exit_code = 7


class DiskFullError(SdaDbError, OSError):
    """The destination or cache file system is full or over quota."""

    code = "disk_full"
    exit_code = 8


def translate(exc: BaseException) -> SdaDbError | None:
    """Map Hugging Face and transport exceptions to SDK errors without leaking URLs or tokens."""
    if isinstance(exc, SdaDbError):
        return exc
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {errno.ENOSPC, errno.EDQUOT}:
        return DiskFullError(f"No space left for the download or cache ({exc.strerror})",
                             hint="Free space, choose another --destination, or move the Hugging Face cache "
                                  "with --cache-dir or HF_HOME to a larger disk.")
    try:
        from huggingface_hub import errors as hub
    except ImportError:  # pragma: no cover - huggingface_hub is a core dependency
        return None
    if isinstance(exc, hub.GatedRepoError):
        return AccessError("This dataset is gated and your account has no approved access", hint=ACCESS_HINT)
    if isinstance(exc, hub.RepositoryNotFoundError):
        return AccessError("Repository not found or not visible with the current credential", hint=ACCESS_HINT)
    # LocalEntryNotFoundError subclasses EntryNotFoundError, so test it first. The Hub also
    # raises it when the network fails and the file is not cached.
    if isinstance(exc, (hub.LocalEntryNotFoundError, hub.OfflineModeIsEnabled)):
        return NetworkError("The file is not in the local cache and could not be fetched (offline or unreachable Hub)",
                            hint="Check connectivity, or run once online at the same release before using --offline.")
    if isinstance(exc, (hub.EntryNotFoundError, hub.RevisionNotFoundError)):
        return NotFoundError(f"{type(exc).__name__}: the requested file or revision does not exist",
                             hint="Check the release name with 'sda-db releases'.")
    if isinstance(exc, hub.HfHubHTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {401, 403}:
            return AccessError(f"The Hub refused the request (HTTP {status})", hint=ACCESS_HINT)
        if status == 404:
            return NotFoundError("The Hub returned HTTP 404", hint="Check the release and sample identifiers.")
        if status == 429:
            return NetworkError("The Hub rate-limited this client (HTTP 429)",
                                hint="Wait a few minutes, lower --workers, or log in for a higher limit.")
        return NetworkError(f"The Hub returned HTTP {status}", hint="Retry later; check status.huggingface.co.")
    name = type(exc).__name__
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException", "RemoteProtocolError",
                "ProxyError", "ConnectionError", "Timeout"} or isinstance(exc, (TimeoutError, ConnectionError)):
        return NetworkError(f"Network failure ({name})",
                            hint="Check connectivity, proxy variables and HF_ENDPOINT; cached releases work with --offline.")
    return None


@contextmanager
def hub_errors() -> Iterator[None]:
    """Re-raise Hub transport and access failures as SDK errors with hints."""
    try:
        yield
    except SdaDbError:
        raise
    except Exception as exc:
        translated = translate(exc)
        if translated is None:
            raise
        raise translated from exc
