"""Credit ledger service — port of Oriexa/src/lib/credits/ledger.ts.
All operations are append-only; entries are never updated or deleted."""

from fastapi import HTTPException
from sqlalchemy import select, update

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import NEW_AGENT_BONUS, NEW_USER_BONUS, PLATFORM_FEE_PERCENT
from app.db.models import CreditTransaction, User


async def _add_credits(
    session: AsyncSession,
    user_id: int,
    amount: int,
    type: str,
    description: str,
    task_id: int | None = None,
) -> dict:
    # Update balance and get new value
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(credit_balance=User.credit_balance + amount)
        .returning(User.credit_balance)
    )
    balance_after = result.scalar_one()

    # Record in append-only ledger
    txn = CreditTransaction(
        user_id=user_id,
        amount=amount,
        type=type,
        task_id=task_id,
        description=description,
        balance_after=balance_after,
    )
    session.add(txn)
    await session.flush()

    return {"balance_after": balance_after}


async def grant_welcome_bonus(session: AsyncSession, user_id: int) -> dict:
    return await _add_credits(session, user_id, NEW_USER_BONUS, "bonus", "Welcome bonus")


async def grant_agent_bonus(session: AsyncSession, operator_id: int) -> dict:
    return await _add_credits(
        session, operator_id, NEW_AGENT_BONUS, "bonus", "Agent registration bonus"
    )


async def process_task_completion(
    session: AsyncSession,
    operator_id: int,
    budget_credits: int,
    task_id: int,
) -> dict:
    fee = budget_credits * PLATFORM_FEE_PERCENT // 100  # integer floor division
    payment = budget_credits - fee

    # Update balance and get new value
    result = await session.execute(
        update(User)
        .where(User.id == operator_id)
        .values(credit_balance=User.credit_balance + payment)
        .returning(User.credit_balance)
    )
    balance_after = result.scalar_one()

    # Record the payment to operator
    txn_payment = CreditTransaction(
        user_id=operator_id,
        amount=payment,
        type="payment",
        task_id=task_id,
        description=f"Task {task_id} completion payment",
        balance_after=balance_after,
    )
    session.add(txn_payment)

    # Record platform fee as tracking entry
    txn_fee = CreditTransaction(
        user_id=operator_id,
        amount=0,
        type="platform_fee",
        task_id=task_id,
        description=f"Platform fee: {fee} credits ({PLATFORM_FEE_PERCENT}% of {budget_credits})",
        balance_after=balance_after,
    )
    session.add(txn_fee)
    await session.flush()

    return {"payment": payment, "fee": fee, "balance_after": balance_after}


async def deduct_credits(
    session: AsyncSession,
    user_id: int,
    amount: int,
    type: str,
    description: str,
    task_id: int | None = None,
) -> dict:
    """
    Deduct credits from a user (e.g., for escrow or payment).
    Raises HTTPException if balance is insufficient.
    """
    # Verify balance first
    result = await session.execute(
        select(User.credit_balance).where(User.id == user_id).limit(1)
    )
    current_balance = result.scalar_one_or_none()
    if current_balance is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    if current_balance < amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Required: {amount}, available: {current_balance}"
        )

    # Update balance (amount is positive, so we subtract)
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(credit_balance=User.credit_balance - amount)
        .returning(User.credit_balance)
    )
    balance_after = result.scalar_one()

    # Record in ledger (negative amount for deduction)
    txn = CreditTransaction(
        user_id=user_id,
        amount=-amount,
        type=type,
        task_id=task_id,
        description=description,
        balance_after=balance_after,
    )
    session.add(txn)
    await session.flush()

    return {"balance_after": balance_after}


async def get_user_transactions(
    session: AsyncSession,
    user_id: int,
    limit: int = 50
) -> list[CreditTransaction]:
    """Retrieve credit transaction history for a user."""
    result = await session.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
