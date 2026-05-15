"""services/document_service.py — Document upload/list/delete (S3-backed)

Rules:
  - PDF only (checked by content type AND extension)
  - Max 20 MB
  - File stored in S3 under documents/<uuid>.pdf
  - DB stores the S3 key (file_path column) instead of a local path
  - Download URLs are S3 presigned URLs (1-hour expiry)
"""
import os
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.document import Document
from models.user import User
from schemas.document import DocumentListResponse, DocumentResponse
from services.space_service import ensure_space_access
from utils.logger import logger
from utils.s3 import delete_file, presigned_url, upload_file

_MAX_SIZE    = 20 * 1024 * 1024   # 20 MB
_ALLOWED_EXT = {".pdf"}
_ALLOWED_CT  = {"application/pdf"}


def _to_resp(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id, space_id=doc.space_id, uploaded_by=doc.uploaded_by,
        filename=doc.filename,
        # Return a presigned URL so the frontend can download directly from S3
        file_path=presigned_url(doc.file_path),
        file_size=doc.file_size, created_at=doc.created_at,
    )


def _validate_file(file: UploadFile) -> None:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF files are allowed")
    if file.content_type not in _ALLOWED_CT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid content type — must be application/pdf")


async def _ingest_into_rag(s3_key: str, space_id: UUID, filename: str) -> None:
    """
    Download the PDF from S3 and send it to the RAG service for ingestion.
    Fire-and-forget — never raises.
    """
    import httpx
    import boto3
    from config import settings

    rag_url = getattr(settings, "RAG_BASE_URL", "http://localhost:8001")
    try:
        s3 = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        obj = s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=s3_key)
        content = obj["Body"].read()

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{rag_url}/ingest/{space_id}",
                files={"file": (filename, content, "application/pdf")},
            )
        if resp.status_code == 200:
            logger.info(f"[RAG] Ingested {filename!r} for space={space_id}")
        else:
            logger.warning(f"[RAG] Ingest returned {resp.status_code} for {filename!r}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"[RAG] Ingest failed (non-blocking) for {filename!r}: {e}")


async def upload_document(db: AsyncSession, space_id: UUID, file: UploadFile, user: User) -> DocumentResponse:
    await ensure_space_access(db, space_id, user)
    _validate_file(file)

    content = await file.read()
    if len(content) > _MAX_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File too large — max {_MAX_SIZE // (1024*1024)} MB")

    # Upload to S3 — returns the S3 key
    s3_key = upload_file(content, "documents", file.filename or "upload.pdf")

    doc = Document(
        space_id=space_id, uploaded_by=user.id,
        filename=file.filename or "upload.pdf",
        file_path=s3_key,          # store S3 key, not a local path
        file_size=len(content),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(f"[DOC] Uploaded: {doc.filename!r} size={len(content)} by {user.email}")

    import asyncio
    asyncio.create_task(_ingest_into_rag(s3_key, space_id, doc.filename))

    return _to_resp(doc)


async def list_documents(db: AsyncSession, space_id: UUID, user: User) -> DocumentListResponse:
    await ensure_space_access(db, space_id, user)
    rows = (await db.execute(
        select(Document)
        .where(Document.space_id == space_id)
        .order_by(Document.created_at.desc())
    )).scalars().all()
    return DocumentListResponse(space_id=space_id, total=len(rows), documents=[_to_resp(d) for d in rows])


async def delete_document(db: AsyncSession, doc_id: UUID, user: User) -> None:
    doc = await db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    await ensure_space_access(db, doc.space_id, user)

    # Delete from S3 first, then remove DB row
    delete_file(doc.file_path)

    await db.delete(doc)
    await db.commit()
    logger.info(f"[DOC] Deleted: {doc.filename!r} by {user.email}")
