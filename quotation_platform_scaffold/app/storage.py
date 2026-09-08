"""
Asset storage abstraction for uploaded logos. Local disk is the default (zero
setup); set STORAGE_BACKEND=s3 for S3-compatible object storage (AWS S3,
MinIO, DigitalOcean Spaces, ...).

boto3 is only imported inside S3Storage — a local-only deployment never
needs it importable, let alone configured.
"""
import os
import uuid
from abc import ABC, abstractmethod


class AssetStorage(ABC):
    @abstractmethod
    def save(self, content: bytes, filename_hint: str, subdir: str) -> str:
        """Persists content, returns the value to store in
        DocTemplate.logo_asset_path (an opaque handle — a local path for
        LocalStorage, an s3:// URI for S3Storage)."""

    @abstractmethod
    def url_for(self, stored_path: str) -> str | None:
        """A browser-servable URL for a previously-saved asset, or None if
        it can't produce one (caller falls back to the /static mount)."""


class LocalStorage(AssetStorage):
    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def save(self, content: bytes, filename_hint: str, subdir: str = "logos") -> str:
        ext = os.path.splitext(filename_hint)[1] or ".png"
        dest_dir = os.path.join(self.base_dir, subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}{ext}")
        with open(dest_path, "wb") as f:
            f.write(content)
        return dest_path

    def url_for(self, stored_path: str) -> str | None:
        # Served by the /static mount in app/main.py, keyed by basename —
        # matches how upload_logo has always named files.
        return f"/static/logos/{os.path.basename(stored_path)}"


class S3Storage(AssetStorage):
    """Deliberately doesn't generate presigned URLs — that needs a live AWS
    call, and url_for() is called from a plain ORM property (DocTemplate.
    logo_url), which shouldn't make network calls per request. Point
    S3_PUBLIC_BASE_URL at a public bucket or a CDN in front of one instead."""

    def __init__(self, bucket: str, region: str | None, endpoint_url: str | None, public_base_url: str | None):
        import boto3

        self.bucket = bucket
        self.public_base_url = public_base_url
        self.client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def save(self, content: bytes, filename_hint: str, subdir: str = "logos") -> str:
        ext = os.path.splitext(filename_hint)[1] or ".png"
        key = f"{subdir}/{uuid.uuid4().hex}{ext}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=_guess_content_type(ext))
        return f"s3://{self.bucket}/{key}"

    def url_for(self, stored_path: str) -> str | None:
        if not stored_path.startswith("s3://"):
            return None
        key = stored_path.split("/", 3)[-1]  # s3://bucket/subdir/name.ext -> subdir/name.ext
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{key}"
        return stored_path  # not directly renderable; caller should configure S3_PUBLIC_BASE_URL


def _guess_content_type(ext: str) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
    }.get(ext.lower(), "application/octet-stream")


_DEFAULT_ASSET_DIR = os.path.join(os.path.dirname(__file__), "..", "uploaded_assets")


def get_asset_storage() -> AssetStorage:
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend == "s3":
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 requires S3_BUCKET to be set.")
        return S3Storage(
            bucket=bucket,
            region=os.environ.get("S3_REGION"),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            public_base_url=os.environ.get("S3_PUBLIC_BASE_URL"),
        )
    return LocalStorage(os.environ.get("ASSET_DIR", _DEFAULT_ASSET_DIR))
