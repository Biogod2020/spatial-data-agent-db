"""SpatialDataAgent-DB client: find, plan, download, verify, read and cite spatial samples.

Quick start::

    import sda_db
    catalog = sda_db.open_catalog()                     # paper release; no login needed to search
    hits = catalog.search("alzheimer hippocampus", organism="human", has_image=True)
    hits.plan_download()                                # sizes before any transfer
    adata = sda_db.load(hits.gsms[0])                   # download (verified) and read as AnnData
"""

from .batch import DownloadPlan, DownloadReport, download, plan_download
from .catalog import DEFAULT_REPO, DEFAULT_REVISION, Catalog
from .download import download_sample, verify_sample
from .errors import (AccessError, DiskFullError, IntegrityError, NetworkError, NotFoundError, SdaDbError, SizeLimitError,
                     UnknownFieldError, UnknownValueError)
from .io import coordinates, image_coordinates, load, read_sample
from .releases import Release, known_releases, resolve_release
from .stream import CacheLimitError, SampleStream, StreamSample, stream_samples

__version__ = "0.4.1"


def open_catalog(release: str | None = None, **options: object) -> Catalog:
    """Open a release catalog (default: the paper release). See `Catalog.open`."""
    return Catalog.open(release, **options)  # type: ignore[arg-type]


__all__ = ["Catalog", "DEFAULT_REPO", "DEFAULT_REVISION", "open_catalog", "load", "read_sample", "coordinates",
           "image_coordinates", "download_sample", "verify_sample", "download", "plan_download", "DownloadPlan",
           "DownloadReport", "CacheLimitError", "SampleStream", "StreamSample", "stream_samples", "Release",
           "known_releases", "resolve_release", "SdaDbError", "AccessError", "DiskFullError", "NotFoundError", "UnknownFieldError",
           "UnknownValueError", "IntegrityError", "NetworkError", "SizeLimitError", "__version__"]
