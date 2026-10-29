"""User-facing access to published SpatialDataAgent-DB releases."""

from .catalog import DEFAULT_REPO, DEFAULT_REVISION, Catalog
from .download import download_sample, verify_sample
from .stream import CacheLimitError, SampleStream, StreamSample, stream_samples

__version__ = "0.2.0"
__all__ = ["Catalog", "DEFAULT_REPO", "DEFAULT_REVISION", "download_sample", "verify_sample",
           "CacheLimitError", "SampleStream", "StreamSample", "stream_samples"]
