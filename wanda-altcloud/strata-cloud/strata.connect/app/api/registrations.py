"""Device registration at dispatch — our side of the unknown contract.

The caller is an external system whose request shape is not yet known; Phase 1 defines this
operation and mocks the inbound flow (the bruno collection and tests act as the caller).
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_edge_secret
from app.db import get_session
from app.schemas.ingest import RegistrationBody, RegistrationOut
from app.services.registration import UnknownIdentifierType, UnknownUser, register_device

router = APIRouter(
    prefix="/device-registrations",
    tags=["device-registrations"],
    dependencies=[Depends(require_edge_secret)],
)
_log = structlog.get_logger(__name__)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RegistrationOut,
    summary="Link a dispatched device to a Member",
)
async def create_registration(
    body: RegistrationBody, session: AsyncSession = Depends(get_session)
) -> RegistrationOut:
    try:
        result = await register_device(
            session,
            user_id=body.user_id,
            identifier_type=body.identifier_type,
            external_id=body.external_id,
        )
        await session.commit()
    except UnknownIdentifierType as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown identifier type {body.identifier_type!r}.",
        ) from exc
    except UnknownUser as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Member with id {body.user_id!r}.",
        ) from exc
    except IntegrityError as exc:
        # Only the kernel's one-active-mapping index means "concurrent registration,
        # retry"; any other integrity failure is a genuine bug and must surface as a 500.
        if "uq_user_external_ids_active" not in str(exc.orig):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent registration for this device; retry.",
        ) from exc

    _log.info(
        "device registered",
        mapping_id=result.mapping.id,
        identifier_type=result.identifier_type,
        superseded=result.superseded_user_id is not None,
    )
    return RegistrationOut(
        id=result.mapping.id,
        user_id=result.mapping.user_id,
        identifier_type=result.identifier_type,
        external_id=result.mapping.external_id,
        registered_at=result.mapping.registered_at,
        superseded_user_id=result.superseded_user_id,
    )
