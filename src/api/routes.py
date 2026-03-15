from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.api.middleware import auth_dependency
from src.config import PANEL_SUB_URL
from src.models.package import list_packages
from src.models.subscription import list_subscriptions, get_subscription_for_client
from src.services.subscription_service import create_sub, delete_sub, renew_sub, APIError

router = APIRouter(prefix="/vpnapi", dependencies=[Depends(auth_dependency)])


class CreateSubscriptionBody(BaseModel):
    packageId: int
    userId: Optional[str] = None


@router.get("/packages")
def get_packages(ct=Depends(auth_dependency)):
    pkgs = list_packages(active_only=True)
    for p in pkgs:
        p.pop("groups", None)
    return pkgs


@router.get("/balance")
def get_balance(ct=Depends(auth_dependency)):
    return {"balance": ct["balance"], "name": ct["name"]}


@router.post("/subscriptions", status_code=201)
def post_subscription(body: CreateSubscriptionBody, ct=Depends(auth_dependency)):
    try:
        return create_sub(ct, body.packageId, body.userId)
    except APIError as e:
        raise HTTPException(e.status_code, str(e))


@router.get("/subscriptions")
def get_subscriptions(ct=Depends(auth_dependency)):
    return list_subscriptions(ct["id"])


@router.get("/subscriptions/{sub_id}")
def get_subscription(sub_id: int, ct=Depends(auth_dependency)):
    sub = get_subscription_for_client(sub_id, ct["id"])
    if not sub:
        raise HTTPException(404, "Not found")
    sub["sub_link"] = f"{PANEL_SUB_URL}/api/files/{sub['panel_subscription_token']}" if sub.get("panel_subscription_token") else None
    return sub


@router.post("/subscriptions/{sub_id}/renew")
def renew_subscription(sub_id: int, ct=Depends(auth_dependency)):
    try:
        sub = renew_sub(ct, sub_id)
        return {"success": True, "subscription": sub}
    except APIError as e:
        raise HTTPException(e.status_code, str(e))


@router.delete("/subscriptions/{sub_id}")
def del_subscription(sub_id: int, ct=Depends(auth_dependency)):
    try:
        sub = delete_sub(sub_id, ct["id"])
        return {"success": True, "subscription": sub}
    except APIError as e:
        raise HTTPException(e.status_code, str(e))
