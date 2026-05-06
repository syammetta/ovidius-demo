"""Cloudflare R2 document storage — cache crawled content to avoid re-fetching.

R2 stores the raw HTML of every crawled page, keyed by a URL-derived path.
On subsequent ingestion runs, we check R2 first and only re-crawl if the
content is missing or stale. This makes ingestion idempotent and fast.

R2 is S3-compatible, so we use boto3 with a custom endpoint.
"""

import hashlib
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


def _url_to_key(url: str) -> str:
    """Convert a URL to an R2 object key."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    safe_path = url.replace("https://", "").replace("http://", "").replace("/", "_")[:100]
    return f"raw/{safe_path}_{url_hash}.html"


def store_document(url: str, content: str, metadata: dict | None = None) -> str:
    """Store raw document content in R2. Returns the object key."""
    client = _get_client()
    key = _url_to_key(url)

    obj_metadata = {
        "source-url": url,
        "crawled-at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        obj_metadata.update({k: str(v) for k, v in metadata.items()})

    client.put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType="text/html",
        Metadata=obj_metadata,
    )
    return key


def get_document(url: str) -> str | None:
    """Retrieve cached document content from R2. Returns None if not found."""
    client = _get_client()
    key = _url_to_key(url)

    try:
        response = client.get_object(Bucket=settings.r2_bucket_name, Key=key)
        return response["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise


def document_exists(url: str) -> bool:
    """Check if a document is cached in R2."""
    client = _get_client()
    key = _url_to_key(url)

    try:
        client.head_object(Bucket=settings.r2_bucket_name, Key=key)
        return True
    except ClientError:
        return False


def list_documents(prefix: str = "raw/") -> list[dict]:
    """List all cached documents in R2."""
    client = _get_client()
    response = client.list_objects_v2(Bucket=settings.r2_bucket_name, Prefix=prefix)

    return [
        {"key": obj["Key"], "size": obj["Size"], "modified": obj["LastModified"].isoformat()}
        for obj in response.get("Contents", [])
    ]
