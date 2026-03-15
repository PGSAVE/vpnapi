from fastapi import Depends, Request, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from src.models.client_token import get_client_token

_bearer = HTTPBearer()


async def auth_dependency(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
):
    ct = get_client_token(credentials.credentials)
    if not ct:
        raise HTTPException(401, "Invalid or inactive token")
    request.state.client_token = ct
    return ct
