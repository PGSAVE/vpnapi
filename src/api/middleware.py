from fastapi import Request, HTTPException
from src.models.client_token import get_client_token


async def auth_dependency(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = auth[7:]
    ct = get_client_token(token)
    if not ct:
        raise HTTPException(401, "Invalid or inactive token")
    request.state.client_token = ct
    return ct
