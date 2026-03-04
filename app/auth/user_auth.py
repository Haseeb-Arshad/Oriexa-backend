from fastapi import Header, HTTPException

async def get_current_user_id(x_user_id: int | None = Header(None, alias="X-User-ID")):
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="Missing X-User-ID header")
    return x_user_id
