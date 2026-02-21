"""POST /api/auth/register — port of Oriexa/src/app/api/auth/register/route.ts
Note: This endpoint does NOT use the standard envelope — returns plain JSON."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.db.engine import get_db
from app.db.models import User
from app.schemas.auth import RegisterRequest
from app.services.credits import grant_welcome_bonus

router = APIRouter()


@router.post("/register")
async def register(request: dict, session: AsyncSession = Depends(get_db)):
    # Validate
    try:
        data = RegisterRequest(**request)
    except (ValidationError, Exception) as e:
        if isinstance(e, ValidationError):
            msg = e.errors()[0].get("msg", "Validation error")
        else:
            msg = str(e)
        return JSONResponse({"error": msg}, status_code=400)

    # Check if user already exists
    result = await session.execute(
        select(User.id).where(User.email == data.email).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return JSONResponse(
            {"error": "An account with this email already exists"},
            status_code=409,
        )

    password_hash = hash_password(data.password)

    # Create user
    new_user = User(
        email=data.email,
        password_hash=password_hash,
        name=data.name,
        role="both",
        credit_balance=0,
    )
    session.add(new_user)
    await session.flush()

    # Grant welcome bonus (500 credits)
    await grant_welcome_bonus(session, new_user.id)
    await session.commit()

    return JSONResponse(
        {"id": new_user.id, "email": new_user.email, "name": new_user.name},
        status_code=201,
    )
