"""Convert the builder's full-panel index (schema 1 JSON) into the SQLite index the client reads.

    python tools/compact_feature_index.py OUT/feature-index.json.gz OUT/feature-index.sqlite.gz

The output is a gzip-compressed SQLite file in the `sda_db.features` format (schema 3): searchable keys map to
the panels that contain them, and full feature lists are stored for targeted panels.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

from sda_db.features import write_index


def main(source: Path, target: Path) -> None:
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        index = json.load(handle)
    plain = target.with_suffix("") if target.suffix == ".gz" else target
    stats = write_index(index["panels"], index["samples"], plain, catalog_sha256=index["catalog_sha256"])
    if target.suffix == ".gz":
        with plain.open("rb") as raw, gzip.open(target, "wb", compresslevel=9) as packed:
            shutil.copyfileobj(raw, packed, length=8 << 20)
    stats.update(sqlite_bytes=plain.stat().st_size, bytes=target.stat().st_size,
                 sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
