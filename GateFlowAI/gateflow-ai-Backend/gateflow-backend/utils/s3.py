"""utils/s3.py — Simple S3 helper (upload, delete, presigned URL)

All file I/O goes through these three functions.
boto3 is lazy-imported so the app still starts if AWS creds are missing in dev.
"""
import uuid
import os
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError

from config import settings
from utils.logger import logger

# One client, reused across requests
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
    return _s3


def upload_file(file_data: bytes, folder: str, original_filename: str) -> str:
    """
    Upload bytes to S3.

    Args:
        file_data:         raw bytes
        folder:            e.g. "documents" or "walkin"
        original_filename: used to derive the extension

    Returns:
        S3 key  e.g. "documents/abc123.pdf"
    """
    ext = os.path.splitext(original_filename)[1] or ""
    key = f"{folder}/{uuid.uuid4().hex}{ext}"
    _client().put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
        Body=file_data,
        ContentType=_content_type(ext),
    )
    logger.info(f"[S3] Uploaded s3://{settings.S3_BUCKET_NAME}/{key}")
    return key


def delete_file(key: str) -> None:
    """Delete an object from S3. Silently ignores missing keys."""
    try:
        _client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        logger.info(f"[S3] Deleted s3://{settings.S3_BUCKET_NAME}/{key}")
    except ClientError as e:
        logger.warning(f"[S3] Delete failed for {key}: {e}")


def presigned_url(key: str, expires: int = 3600) -> str:
    """
    Generate a presigned GET URL valid for `expires` seconds (default 1 hour).
    Returns empty string if key is empty/None.
    """
    if not key:
        return ""
    try:
        url = _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expires,
        )
        return url
    except ClientError as e:
        logger.warning(f"[S3] Presign failed for {key}: {e}")
        return ""


def _content_type(ext: str) -> str:
    return {
        ".pdf":  "application/pdf",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".webp": "image/webp",
    }.get(ext.lower(), "application/octet-stream")
