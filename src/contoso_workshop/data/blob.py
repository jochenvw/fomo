"""Blob operations for one participant container: idempotent upload, listing, metadata merge.

Metadata writes use optimistic concurrency (ETag / If-Match) and never drop keys they
did not set. Blob metadata rules: keys are C#-identifier-like, values ASCII, ~8 KB total.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from azure.core import MatchConditions
from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceModifiedError
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings

from ..errors import PermissionDenied, WorkshopError

META_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_METADATA_BYTES = 8 * 1024

# Keys owned by the workshop; written on upload (source) or by the miner (classification).
SOURCE_KEYS = ("doc_id", "title", "source_sha256", "corpus_version", "workshop")
CLASSIFICATION_KEYS = (
    "document_type",
    "classification_confidence",
    "classification_version",
    "classification_model",
    "classification_reason",
    "classified_sha256",
    "classified_at",
)

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

SKIP_PREFIXES = ("_", ".")  # manifests / outputs are never classified


def validate_metadata(meta: dict[str, str]) -> None:
    total = 0
    for key, value in meta.items():
        if not META_KEY_RE.match(key):
            raise WorkshopError(f"invalid blob metadata key '{key}'")
        if not isinstance(value, str) or not value.isascii():
            raise WorkshopError(f"blob metadata '{key}' must be an ASCII string")
        total += len(key) + len(value)
    if total > MAX_METADATA_BYTES:
        raise WorkshopError(f"blob metadata too large ({total} bytes > {MAX_METADATA_BYTES})")


def merge_metadata(existing: dict[str, str], updates: dict[str, str]) -> dict[str, str]:
    """Keep every pre-existing key; overwrite only keys in `updates`. Keys are case-insensitive."""
    lower_updates = {k.lower(): k for k in updates}
    merged = {k: v for k, v in existing.items() if k.lower() not in lower_updates}
    merged.update(updates)
    validate_metadata(merged)
    return merged


@dataclass
class BlobInfo:
    name: str
    etag: str
    size: int
    metadata: dict[str, str]
    last_modified: Any


class ParticipantContainer:
    def __init__(self, blob_endpoint: str, container: str, credential: TokenCredential) -> None:
        self.service = BlobServiceClient(account_url=blob_endpoint, credential=credential)
        self.container_name = container
        self.client: ContainerClient = self.service.get_container_client(container)

    def exists(self) -> bool:
        try:
            return bool(self.client.exists())
        except HttpResponseError as exc:
            raise _wrap(exc, f"check container {self.container_name}") from exc

    def ensure(self, metadata: dict[str, str] | None = None) -> bool:
        """Create container if missing. Returns True if created."""
        try:
            self.client.create_container(metadata=metadata)
            return True
        except ResourceExistsError:
            return False
        except HttpResponseError as exc:
            raise _wrap(exc, f"create container {self.container_name}") from exc

    def list(self, *, include_metadata: bool = True) -> Iterator[BlobInfo]:
        try:
            for b in self.client.list_blobs(include=["metadata"] if include_metadata else None):
                if b.name.startswith(SKIP_PREFIXES):
                    continue
                yield BlobInfo(b.name, b.etag, b.size, dict(b.metadata or {}), b.last_modified)
        except HttpResponseError as exc:
            raise _wrap(exc, f"list {self.container_name}") from exc

    def get(self, name: str) -> BlobInfo | None:
        bc = self.client.get_blob_client(name)
        try:
            props = bc.get_blob_properties()
        except HttpResponseError as exc:
            if exc.status_code == 404:
                return None
            raise _wrap(exc, f"get {name}") from exc
        return BlobInfo(name, props.etag, props.size, dict(props.metadata or {}), props.last_modified)

    def download(self, name: str) -> tuple[bytes, str]:
        bc = self.client.get_blob_client(name)
        try:
            stream = bc.download_blob()
            return stream.readall(), stream.properties.etag
        except HttpResponseError as exc:
            raise _wrap(exc, f"download {name}") from exc

    def upload(
        self, name: str, data: bytes, metadata: dict[str, str], *, overwrite_if_changed: bool = False
    ) -> str:
        """Idempotent upload. Returns uploaded | unchanged | conflict.

        - missing blob -> upload
        - same source_sha256 -> unchanged (no write)
        - different content -> conflict unless overwrite_if_changed (explicit approval)
        """
        validate_metadata(metadata)
        existing = self.get(name)
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        settings = ContentSettings(content_type=CONTENT_TYPES.get(suffix, "application/octet-stream"))
        bc = self.client.get_blob_client(name)
        if existing is not None:
            if existing.metadata.get("source_sha256") == metadata.get("source_sha256"):
                return "unchanged"
            if not overwrite_if_changed:
                return "conflict"
            try:
                bc.upload_blob(data, overwrite=True, metadata=metadata, content_settings=settings,
                               etag=existing.etag, match_condition=MatchConditions.IfNotModified)
            except ResourceModifiedError as exc:
                raise WorkshopError(f"{name} changed during upload; rerun") from exc
            return "uploaded"
        try:
            bc.upload_blob(data, overwrite=False, metadata=metadata, content_settings=settings)
        except ResourceExistsError:
            return "conflict"
        except HttpResponseError as exc:
            raise _wrap(exc, f"upload {name}") from exc
        return "uploaded"

    def update_metadata(self, name: str, updates: dict[str, str], *, etag: str | None = None,
                        attempts: int = 3) -> dict[str, str]:
        """Merge `updates` into blob metadata with If-Match. Re-reads on concurrent change."""
        bc = self.client.get_blob_client(name)
        for _ in range(attempts):
            props = bc.get_blob_properties()
            if etag is not None and props.etag != etag:
                raise WorkshopError(
                    f"{name} changed since it was read (etag mismatch); it will be reclassified next run"
                )
            merged = merge_metadata(dict(props.metadata or {}), updates)
            try:
                bc.set_blob_metadata(merged, etag=props.etag, match_condition=MatchConditions.IfNotModified)
                return merged
            except ResourceModifiedError:
                continue
            except HttpResponseError as exc:
                raise _wrap(exc, f"set metadata on {name}") from exc
        raise WorkshopError(f"could not update metadata on {name}: concurrent modifications")

    def remove_metadata_keys(self, name: str, keys: tuple[str, ...]) -> None:
        bc = self.client.get_blob_client(name)
        props = bc.get_blob_properties()
        lowered = {k.lower() for k in keys}
        kept = {k: v for k, v in (props.metadata or {}).items() if k.lower() not in lowered}
        bc.set_blob_metadata(kept, etag=props.etag, match_condition=MatchConditions.IfNotModified)

    def delete_blob(self, name: str) -> None:
        self.client.delete_blob(name)

    def delete_container(self) -> None:
        self.client.delete_container()


def _wrap(exc: HttpResponseError, what: str) -> WorkshopError:
    if exc.status_code in (401, 403):
        reason = f"{exc.reason} {exc.message or ''}".lower()
        if "network security perimeter" in reason or "authorizationfailure" in reason.replace(" ", ""):
            return PermissionDenied(
                f"{what}: HTTP {exc.status_code} ({exc.reason}). Storage network rules rejected this caller.",
                fix="network, not RBAC: perimeter mode admits only Search and listed IPs. Use storageNetworkMode="
                "'public' (Entra-only) or add your egress IP (see docs/troubleshooting.md)",
                owner="facilitator",
            )
        return PermissionDenied(
            f"{what}: HTTP {exc.status_code} ({exc.reason}). Needs Storage Blob Data Contributor.",
            fix="ask the facilitator to grant Storage Blob Data Contributor (see docs/security-and-rbac.md); "
            "new role assignments can take up to 10 minutes to apply",
            owner="facilitator",
        )
    return WorkshopError(f"{what}: HTTP {exc.status_code} {exc.message}")
