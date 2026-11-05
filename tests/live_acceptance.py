"""Bounded live acceptance against the Hub (not part of the offline unit tests).

    python tests/live_acceptance.py --destination /tmp/sda-live [--skip-download]

Searches the default release (paper data + metadata v2) and the paper release without a token, then (with approved access) plans and downloads two small
samples with images (~10 MB), verifies, reads and cuts patches, and drives `sda-db mcp` over stdio.
Prints one JSON record; exits nonzero on the first failed check. Downloads nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SMALL = ["GSM9448988", "GSM8915497"]  # Xenium DAPI 4.4 MB and Visium H&E 5.7 MB, images included


def check(record: dict, name: str, condition: bool, detail: object) -> None:
    record["checks"].append({"check": name, "ok": bool(condition), "detail": detail})
    if not condition:
        print(json.dumps(record, indent=2, default=str))
        raise SystemExit(f"FAILED: {name}")


def mcp_session(executable: str, env: dict[str, str]) -> dict:
    process = subprocess.Popen([executable, "mcp", "--max-download-size", "30MB"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    def ask(identifier: int, method: str, params: dict) -> dict:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())
    try:
        init = ask(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "live-acceptance", "version": "1"}})
        tools = ask(2, "tools/list", {})["result"]["tools"]
        search = ask(3, "tools/call", {"name": "search_samples",
                                       "arguments": {"query": "glioblastoma", "has_image": True, "limit": 2}})
        big = ask(4, "tools/call", {"name": "download_samples", "arguments": {"platform": "Xenium", "confirm": True}})
        return {"protocol": init["result"]["protocolVersion"], "tools": len(tools),
                "search_total": search["result"]["structuredContent"]["total"],
                "large_download_refused": big["result"]["structuredContent"].get("error", {}).get("code")}
    finally:
        process.stdin.close()
        process.wait(timeout=30)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--skip-download", action="store_true", help="only the ungated search checks")
    args = parser.parse_args()
    import sda_db
    from sda_db.image import read_patch
    from sda_db.io import image_coordinates

    record: dict = {"client": sda_db.__version__, "python": sys.version.split()[0], "checks": []}
    started = time.monotonic()
    anonymous = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
    anonymous["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    anonymous["HF_HOME"] = str(args.destination / "anonymous-hf")
    executable = str(Path(sys.executable).with_name("sda-db"))
    out = subprocess.run([executable, "search", "alzheimer", "--json", "--limit", "1"], capture_output=True,
                         text=True, env=anonymous, timeout=300)
    payload = json.loads(out.stdout or "{}")
    check(record, "ungated search without a token", out.returncode == 0 and payload.get("total", 0) > 0,
          {"total": payload.get("total"), "catalog_source": payload.get("catalog_source")})
    out = subprocess.run([executable, "search", "--gene", "GFAP", "--gene", "AQP4", "--platform", "Xenium", "--json",
                          "--limit", "1"], capture_output=True, text=True, env={**anonymous,
                          "SDA_DB_CACHE": str(args.destination / "anonymous-sda")}, timeout=600)
    payload = json.loads(out.stdout or "{}")
    check(record, "gene search without a token", out.returncode == 0 and payload.get("total", 0) > 0,
          {"total": payload.get("total"), "matched": (payload.get("samples") or [{}])[0].get("matched_features")})
    out = subprocess.run([executable, "search", "human", "lung", "fibrosis", "Visium", "--organ", "lung",
                          "--min-confidence", "0.6", "--json", "--limit", "1"], capture_output=True, text=True,
                         env=anonymous, timeout=300)
    payload = json.loads(out.stdout or "{}")
    check(record, "metadata v2 search without a token", out.returncode == 0 and payload.get("total", 0) > 0
          and payload.get("metadata") == "metadata-v2.1-20261105",
          {"total": payload.get("total"), "metadata": payload.get("metadata")})
    curated = sda_db.open_catalog()
    paper = sda_db.open_catalog("paper")
    check(record, "default release = paper data + metadata v2",
          (curated.release_name, len(curated), curated.revision == paper.revision, bool(curated.metadata_v2))
          == ("v1.1.0-20261105", 12163, True, True),
          {"commit": curated.revision, "metadata": (curated.metadata_v2 or {}).get("tag"),
           "platform_changes": sum(1 for r in curated.rows if r["platform"] != r["platform_v3"])})
    catalog = paper
    check(record, "paper release unchanged", (paper.release_name, len(paper), paper.metadata_v2,
                                              "organ" in paper.rows[0]) == ("v1.0.0-20261025", 12163, None, False),
          {"commit": paper.revision, "studies": len(paper.list_studies())})
    record["release"] = {"name": catalog.release_name, "commit": catalog.revision, "source": catalog.source}
    if args.skip_download:
        print(json.dumps(record, indent=2, default=str))
        return
    selection = catalog.select(SMALL)
    plan = selection.plan_download(destination=args.destination / "downloads", include_image=True)
    check(record, "plan from the release manifest", plan.total_bytes is not None and plan.total_bytes < 20e6,
          plan.to_dict(per_sample=False))
    report = selection.download(args.destination / "downloads", include_image=True, max_bytes="20MB", progress=False)
    check(record, "parallel verified download", report.ok and len(report.completed) == 2, report.to_dict()["summary"])
    verified = [sda_db.verify_sample(d, require_image=True)["receipt_verified"] for d in report.directories]
    check(record, "offline verification with receipts", all(verified), verified)
    adata = sda_db.load(SMALL[1], catalog=catalog, destination=args.destination / "downloads")
    meta = json.loads(adata.uns["sda_db"]["sample_json"])
    patch, _ = read_patch(adata.uns["sda_db"]["directory"], image_coordinates(adata, meta)[0], 64)
    check(record, "load and image patch", adata.n_obs > 0 and patch.shape[:2] == (64, 64),
          {"shape": list(adata.shape), "patch": list(patch.shape)})
    env = {**os.environ, "SDA_DB_DOWNLOAD_DIR": str(args.destination / "mcp")}
    session = mcp_session(executable, env)
    check(record, "MCP stdio session", session["tools"] == 13 and session["large_download_refused"] == "size_limit",
          session)
    latest = sda_db.open_catalog("latest")
    check(record, "latest resolves to a commit", len(latest.revision) == 40 and len(latest) >= len(catalog),
          {"commit": latest.revision, "samples": len(latest)})
    record["elapsed_seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    main()
