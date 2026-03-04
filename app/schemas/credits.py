from pydantic import BaseModel


class CreditTransactionResponse(BaseModel):
    id: int
    amount: int
    type: str
    task_id: int | None
    description: str | None
    balance_after: int
    created_at: str
