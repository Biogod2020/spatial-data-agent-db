"""Build the measured-feature index of a release (maintainers; needs h5py, anndata and the release files).

    python tools/build_feature_index.py --release-root RELEASE --catalog samples.csv \
        --manifest file-manifest.jsonl --out OUT [--workers 48] [--hash-sample 100]

Reads only the `var` table of every sample's spatial.h5ad (never the matrix). Identical feature lists are
stored once as a "panel". Writes OUT/feature-index.json.gz:

    {"schema": "sda-db-feature-index-1", "catalog_sha256": ..., "panels": [[[name, id, type], ...], ...],
     "samples": {gsm: panel_number}}

Before reading, each file's size is checked against the release file manifest, and a random subset is
re-hashed against the catalog's expression_sha256. The script reports how many files were checked.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time


# HDF5 file locking over NFS can slow var reads by orders of magnitude; the release is read-only.
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")


def read_features(path: str) -> tuple[list[list[str]], str]:
    import anndata
    import h5py

    with h5py.File(path, "r") as handle:
        var = anndata.io.read_elem(handle["var"])
    names = var["feature_name"].astype(str).tolist() if "feature_name" in var else list(map(str, var.index))
    ids = var["source_feature_id"].astype(str).tolist() if "source_feature_id" in var else list(map(str, var.index))
    types = var["feature_type"].astype(str).tolist() if "feature_type" in var else [""] * len(names)
    rows = [[n, i, t] for n, i, t in zip(names, ids, types)]
    key = hashlib.sha256("\n".join("\t".join(r) for r in rows).encode()).hexdigest()
    return rows, key


def worker(task: tuple[str, str, int]) -> dict:
    """Pass 1: the panel key of one sample (rows stay in the worker)."""
    gsm, path, expected_vars = task
    try:
        rows, key = read_features(path)
        return {"gsm": gsm, "key": key, "n": len(rows), "path": path, "ok": len(rows) == expected_vars}
    except Exception as exc:  # noqa: BLE001 - reported
        return {"gsm": gsm, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def panel_rows_of(path: str) -> tuple[str, list[list[str]]]:
    """Pass 2: the feature rows of one representative sample per panel."""
    rows, key = read_features(path)
    return key, rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--hash-sample", type=int, default=100)
    args = parser.parse_args()
    started = time.monotonic()
    catalog_sha = sha256(args.catalog)
    rows = list(csv.DictReader(args.catalog.open(encoding="utf-8-sig", newline="")))
    sizes = {}
    for line in args.manifest.open(encoding="utf-8"):
        if line.strip():
            record = json.loads(line)
            sizes[record["path"]] = record["bytes"]
    tasks, problems = [], []
    for row in rows:
        relative = f"{row['sample_dir']}/spatial.h5ad"
        path = args.release_root / relative
        if not path.is_file() or path.stat().st_size != sizes.get(relative):
            problems.append({"gsm": row["gsm"], "problem": "missing file or size differs from the manifest"})
            continue
        tasks.append((row["gsm"], str(path), int(row["n_vars"])))
    by_gsm = {row["gsm"]: row for row in rows}
    hashed = random.Random(0).sample(tasks, min(args.hash_sample, len(tasks)))
    for gsm, path, _ in hashed:
        if sha256(Path(path)) != by_gsm[gsm]["expression_sha256"]:
            problems.append({"gsm": gsm, "problem": "SHA256 differs from the catalog"})
    print(f"size-checked {len(tasks)} files, hashed {len(hashed)}, problems {len(problems)}", file=sys.stderr)
    keys: dict[str, str] = {}
    representative: dict[str, str] = {}
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(worker, tasks, chunksize=4):
            done += 1
            if not result["ok"]:
                problems.append({"gsm": result["gsm"], "problem": result.get("error") or
                                 f"var has {result.get('n')} features, catalog n_vars differs"})
                continue
            keys[result["gsm"]] = result["key"]
            representative.setdefault(result["key"], result["path"])
            if done % 500 == 0:
                print(f"{done}/{len(tasks)} samples, {len(representative)} panels", file=sys.stderr)
        ordered = sorted(representative, key=lambda k: min(g for g, v in keys.items() if v == k))
        found = dict(pool.map(panel_rows_of, [representative[k] for k in ordered], chunksize=2))
    panels = {key: number for number, key in enumerate(ordered)}
    panel_rows = [found[key] for key in ordered]
    samples = {gsm: panels[key] for gsm, key in sorted(keys.items())}
    args.out.mkdir(parents=True, exist_ok=True)
    index = {"schema": "sda-db-feature-index-1", "catalog_sha256": catalog_sha, "samples": samples,
             "panels": panel_rows}
    target = args.out / "feature-index.json.gz"
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(index, handle, ensure_ascii=False, separators=(",", ":"))
    report = {"catalog_sha256": catalog_sha, "samples_indexed": len(samples), "samples_in_catalog": len(rows),
              "panels": len(panel_rows), "feature_rows": sum(len(p) for p in panel_rows),
              "files_size_checked": len(tasks), "files_hashed": len(hashed), "problems": problems,
              "index_bytes": target.stat().st_size, "index_sha256": sha256(target),
              "elapsed_seconds": round(time.monotonic() - started, 1)}
    (args.out / "feature-index-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "problems"} | {"problems": len(problems)}, indent=2))


if __name__ == "__main__":
    main()
