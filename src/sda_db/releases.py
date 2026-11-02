"""Named database releases: reproducible commits, aliases and ungated catalog mirrors."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from importlib import resources
import re
from typing import Any

from .errors import NotFoundError, hub_errors

COMMIT = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class Release:
    """A database release bound to one full Hub commit."""

    name: str
    repo_id: str
    revision: str
    description: str = ""
    tag: str | None = None
    sample_count: int | None = None
    source_release: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    public_repo_id: str | None = None
    public_revision: str | None = None
    rolling: bool = False
    feature_index: dict[str, str] | None = None

    @property
    def short(self) -> str:
        return self.revision[:7]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "repo_id": self.repo_id, "revision": self.revision, "tag": self.tag,
                "sample_count": self.sample_count, "source_release": self.source_release,
                "public_catalog": (f"{self.public_repo_id}@{self.public_revision}" if self.public_repo_id else None),
                "rolling": self.rolling, "feature_index": bool(self.feature_index), "description": self.description}


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    """The release registry shipped with this client version."""
    text = resources.files("sda_db").joinpath("releases.json").read_text(encoding="utf-8")
    value = json.loads(text)
    if value.get("schema") != "sda-db-client-releases-2":
        raise ValueError("Unsupported release registry schema")
    return value


def _from_record(record: dict[str, Any], repo_id: str) -> Release:
    public = record.get("public_catalog") or {}
    return Release(name=record["name"], repo_id=repo_id, revision=record["revision"],
                   description=record.get("description", ""), tag=record.get("tag"),
                   sample_count=record.get("sample_count"), source_release=record.get("source_release"),
                   files=dict(record.get("files", {})), public_repo_id=public.get("repo_id"),
                   public_revision=public.get("revision"), feature_index=record.get("feature_index"))


def known_releases() -> list[Release]:
    reg = registry()
    return [_from_record(record, reg["repo_id"]) for record in reg["releases"]]


def default_release() -> Release:
    return resolve_release(None)


def resolve_release(name: str | None = None, *, repo_id: str | None = None,
                    offline: bool = False) -> Release:
    """Resolve a release name, alias, tag, branch or full commit to a fixed commit.

    Named releases resolve without network access. A branch such as ``latest``/``main``
    is resolved once, online, to the commit that every later file request uses.
    """
    reg = registry()
    repo = repo_id or reg["repo_id"]
    wanted = (name or reg["default"]).strip()
    if repo == reg["repo_id"]:
        for record in reg["releases"]:
            if wanted == record["name"] or wanted in record.get("aliases", []) or wanted == record.get("tag"):
                return _from_record(record, repo)
            if COMMIT.fullmatch(wanted) and wanted == record["revision"]:
                return _from_record(record, repo)
    if COMMIT.fullmatch(wanted):
        return Release(name=wanted[:7], repo_id=repo, revision=wanted, description="Explicit commit")
    latest = reg["latest"]
    ref = latest["ref"] if wanted in {latest["name"], *latest.get("aliases", [])} else wanted
    if offline:
        raise NotFoundError(f"Offline mode cannot resolve the moving reference {wanted!r}",
                            hint="Use a named release (sda-db releases) or a full 40-character commit offline.")
    from huggingface_hub import HfApi

    with hub_errors():
        try:
            sha = HfApi().repo_info(repo, repo_type="dataset", revision=ref).sha
        except Exception as exc:
            from huggingface_hub.errors import RevisionNotFoundError
            if isinstance(exc, RevisionNotFoundError):
                names = ", ".join(r["name"] for r in reg["releases"])
                raise NotFoundError(f"Unknown release {wanted!r}",
                                    hint=f"Known releases: {names}, latest; or pass a full commit.") from exc
            raise
    if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
        raise NotFoundError("The Hub did not return a full commit hash")
    return Release(name=wanted if wanted != ref else ref, repo_id=repo, revision=sha,
                   description=latest["description"] if ref == latest["ref"] else f"Hub reference {ref}",
                   rolling=True)
