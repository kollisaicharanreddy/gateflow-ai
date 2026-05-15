"""services/walkin_service.py — Walk-in approval business logic

Walk-in flow:
  1. Guard creates WalkInRequest (PENDING)
  2. Organizer approves → service creates a normal Invite (type=WALKIN)
     and links it back to the WalkInRequest row
  3. Organizer rejects → WalkInRequest status = REJECTED

All QR scanning uses the SAME existing invite/entry flow — no new pipeline.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.invite import Invite, InviteStatus, InviteType
from models.space import Space
from models.user import User, UserRole
from models.walkin import WalkInRequest, WalkInStatus
from schemas.walkin import (
    WalkInApprovedResponse,
    WalkInCreateRequest,
    WalkInListResponse,
    WalkInRejectRequest,
    WalkInResponse,
)
from services.invite_service import generate_invite_token, generate_qr_token, _link
from services.space_service import ensure_space_access
from utils.logger import logger
from utils.s3 import upload_file, presigned_url

_PROOF_FOLDER = "walkin"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_resp(req: WalkInRequest) -> WalkInResponse:
    return WalkInResponse(
        id=req.id, space_id=req.space_id, requested_by=req.requested_by,
        visitor_name=req.visitor_name, visitor_phone=req.visitor_phone,
        reason=req.reason,
        # Convert S3 key → presigned URL so frontend can display the image
        proof_image=presigned_url(req.proof_image) if req.proof_image else None,
        status=req.status,
        rejected_note=req.rejected_note, invite_id=req.invite_id,
        created_at=req.created_at, updated_at=req.updated_at,
    )


async def _get_space(db: AsyncSession, space_id: UUID) -> Space:
    space = await db.get(Space, space_id)
    if space is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Space {space_id} not found")
    if not space.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Space is inactive")
    if not space.walkin_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Walk-ins are disabled for this space")
    return space


async def _get_walkin(db: AsyncSession, walkin_id: UUID) -> WalkInRequest:
    req = await db.get(WalkInRequest, walkin_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Walk-in request not found")
    return req


def _save_proof_image(file: UploadFile) -> str:
    """Upload proof image to S3. Returns the S3 key."""
    content = file.file.read()
    return upload_file(content, _PROOF_FOLDER, file.filename or "proof.jpg")


# ── Public service functions ──────────────────────────────────────────────────

async def create_walkin_request(
    db: AsyncSession,
    data: WalkInCreateRequest,
    guard: User,
    proof_image: UploadFile | None,
) -> WalkInResponse:
    """Guard creates a new walk-in request (status = PENDING)."""
    await _get_space(db, data.space_id)
    if guard.role != UserRole.ADMIN:
        from services.guard_space_service import ensure_guard_assigned_to_space
        await ensure_guard_assigned_to_space(db, guard, data.space_id)

    proof_path = _save_proof_image(proof_image) if proof_image else None

    req = WalkInRequest(
        space_id=data.space_id, requested_by=guard.id,
        visitor_name=data.visitor_name, visitor_phone=data.visitor_phone,
        reason=data.reason, proof_image=proof_path,
        status=WalkInStatus.PENDING,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    logger.info(f"[WALKIN] Request created: {req.visitor_name!r} by {guard.email}")
    # Notify space owner (in-app)
    space = await db.get(Space, req.space_id)
    if space:
        from models.notification import NotificationType
        from services.notification_service import create_notification

        msg = f"{req.visitor_name} requested entry"
        if req.reason:
            msg = f"{msg}. Reason: {req.reason}"
        await create_notification(
            db,
            space.owner_id,
            NotificationType.WALKIN_REQUEST,
            "New walk-in request",
            msg,
            space_id=req.space_id,
        )
        await db.commit()
    # Notify live dashboard clients
    from websocket.dashboard_ws import broadcast_walkin
    await broadcast_walkin(req.space_id, req.visitor_name, req.id)
    return _to_resp(req)


async def approve_walkin_request(
    db: AsyncSession,
    walkin_id: UUID,
    approver: User,
    *,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    origin: str | None = None,
    referer: str | None = None,
) -> WalkInApprovedResponse:
    """
    Organizer approves a walk-in request.
    Creates a normal Invite (type=WALKIN) so the visitor can be scanned
    with the SAME existing QR entry flow — nothing new to learn.
    Organizer may optionally supply valid_from / valid_until; defaults to now / now+12h.
    """
    logger.debug(f"[WALKIN] approve_walkin_request START walkin_id={walkin_id} approver={approver.email}")

    req = await _get_walkin(db, walkin_id)
    logger.debug(f"[WALKIN] req loaded: status={req.status} space_id={req.space_id}")

    if req.status != WalkInStatus.PENDING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Request is already {req.status.value}")

    # Load space first — ensure_space_access also checks ownership/assignment
    space = await db.get(Space, req.space_id)
    logger.debug(f"[WALKIN] space loaded: {space}")
    if space is None or not space.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found or inactive")

    await ensure_space_access(db, req.space_id, approver)
    logger.debug("[WALKIN] space access verified")

    now = datetime.now(timezone.utc)

    # Use organizer-supplied times if provided, otherwise fall back to defaults
    if valid_from is not None:
        vf = valid_from if valid_from.tzinfo else valid_from.replace(tzinfo=timezone.utc)
    else:
        vf = now

    if valid_until is not None:
        vu = valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
    else:
        # Default: 12 h from now, capped by space end_time if set
        vu = now + timedelta(hours=12)
        if space.end_time:
            space_end = space.end_time if space.end_time.tzinfo else space.end_time.replace(tzinfo=timezone.utc)
            vu = min(vu, space_end)
        if vu <= now:
            vu = now + timedelta(hours=12)

    if vu <= vf:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "valid_until must be after valid_from")

    logger.debug(f"[WALKIN] valid_from={vf} valid_until={vu}")

    # Build invite — reuses the SAME token generation from invite_service
    invite_id = uuid.uuid4()
    logger.debug(f"[WALKIN] generating invite_token for invite_id={invite_id}")
    invite_token = generate_invite_token(
        str(invite_id), str(req.space_id), InviteType.WALKIN, vf, vu,
    )
    qr_token = generate_qr_token()
    logger.debug(f"[WALKIN] qr_token={qr_token}")

    invite = Invite(
        id=invite_id, space_id=req.space_id, created_by=approver.id,
        visitor_name=req.visitor_name, visitor_phone=req.visitor_phone,
        invite_type=InviteType.WALKIN,
        invite_token=invite_token, qr_token=qr_token,
        valid_from=vf, valid_until=vu,
        status=InviteStatus.ACTIVE,
    )
    db.add(invite)
    logger.debug("[WALKIN] invite added to session, updating req status")

    # Update walk-in request
    req.status    = WalkInStatus.APPROVED
    req.invite_id = invite_id

    logger.debug("[WALKIN] calling db.commit()")
    await db.commit()
    logger.debug("[WALKIN] db.commit() done, refreshing")
    await db.refresh(invite)
    await db.refresh(req)
    logger.info(f"[WALKIN] Approved: {req.visitor_name!r} by {approver.email}, invite={invite_id}")

    return WalkInApprovedResponse(
        walkin_id=req.id, status=req.status,
        invite_id=invite.id, invite_link=_link(invite.invite_token, origin=origin, referer=referer),
        qr_token=invite.qr_token, visitor_name=invite.visitor_name,
        valid_from=invite.valid_from, valid_until=invite.valid_until,
    )


async def reject_walkin_request(
    db: AsyncSession,
    walkin_id: UUID,
    data: WalkInRejectRequest,
    approver: User,
) -> WalkInResponse:
    """Organizer rejects a walk-in request."""
    req = await _get_walkin(db, walkin_id)

    if req.status != WalkInStatus.PENDING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Request is already {req.status.value}")

    await ensure_space_access(db, req.space_id, approver)

    req.status        = WalkInStatus.REJECTED
    req.rejected_note = data.note
    await db.commit()
    await db.refresh(req)
    logger.info(f"[WALKIN] Rejected: {req.visitor_name!r} by {approver.email}")
    return _to_resp(req)


async def get_pending_requests(
    db: AsyncSession,
    space_id: UUID | None,
    user: User,
) -> WalkInListResponse:
    """List PENDING walk-ins: organizers see owned spaces; guards see assigned spaces only."""
    filters = [WalkInRequest.status == WalkInStatus.PENDING]

    from services.guard_space_service import is_guard_like, list_assigned_space_ids

    if is_guard_like(user.role):
        assigned = await list_assigned_space_ids(db, user.id)
        if not assigned:
            return WalkInListResponse(total=0, requests=[])
        filters.append(WalkInRequest.space_id.in_(assigned))
        if space_id:
            if space_id not in assigned:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to this space")
            filters.append(WalkInRequest.space_id == space_id)
    elif user.role != UserRole.ADMIN:
        from models.space import Space as SpaceModel
        owned_space_ids = (await db.execute(
            select(SpaceModel.id).where(SpaceModel.owner_id == user.id)
        )).scalars().all()
        filters.append(WalkInRequest.space_id.in_(owned_space_ids))
        if space_id:
            await ensure_space_access(db, space_id, user)
            filters.append(WalkInRequest.space_id == space_id)
    else:
        if space_id:
            filters.append(WalkInRequest.space_id == space_id)

    rows = (await db.execute(
        select(WalkInRequest).where(*filters).order_by(WalkInRequest.created_at.desc())
    )).scalars().all()

    return WalkInListResponse(total=len(rows), requests=[_to_resp(r) for r in rows])
