import secrets
from datetime import datetime, timedelta, timezone

from src.config import PANEL_SUB_URL
from src.models.package import get_package
from src.models.client_token import get_client_token_by_id, update_balance
from src.models.subscription import create_subscription, get_subscription_for_client, mark_deleted, update_expires_at
from src.models.transaction import create_transaction
from src.services.panel_api import create_user, delete_user, update_user
import httpx


class APIError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def create_sub(client_token, package_id, user_id=None):
    pkg = get_package(package_id)
    if not pkg or not pkg["active"]:
        raise APIError("Package not found", 404)

    if client_token["balance"] < pkg["price"]:
        raise APIError("Insufficient balance", 402)

    panel_user_id = f"api_{user_id}_{secrets.token_hex(4)}" if user_id else f"api_{secrets.token_hex(4)}_{secrets.token_hex(4)}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=pkg["duration_days"])).isoformat()

    try:
        panel_user = create_user(
            user_id=panel_user_id,
            username=panel_user_id,
            groups=pkg["groups"],
            traffic_limit_gb=pkg["traffic_limit_gb"],
            expire_at=expires_at,
        )
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("message", e.response.text)
        except Exception:
            detail = e.response.text
        raise APIError(f"Panel error: {detail}", 502)

    update_balance(client_token["id"], -pkg["price"])

    sub = create_subscription(
        client_token_id=client_token["id"],
        package_id=pkg["id"],
        panel_user_id=panel_user["userId"],
        panel_subscription_token=panel_user.get("subscriptionToken"),
        expires_at=expires_at,
    )

    create_transaction(
        client_token_id=client_token["id"],
        amount=-pkg["price"],
        tx_type="charge",
        description=f"Subscription: {pkg['name']}",
        subscription_id=sub["id"],
    )

    sub_link = f"{PANEL_SUB_URL}/api/files/{panel_user.get('subscriptionToken')}" if panel_user.get("subscriptionToken") else None
    return {**sub, "sub_link": sub_link}


def renew_sub(client_token, sub_id):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Cannot renew deleted subscription", 400)

    pkg = get_package(sub["package_id"])
    if not pkg:
        raise APIError("Package not found", 404)

    if client_token["balance"] < pkg["price"]:
        raise APIError("Insufficient balance", 402)

    # Extend from current expiry or from now if already expired
    current_exp = datetime.fromisoformat(sub["expires_at"].replace("Z", "+00:00"))
    if current_exp.tzinfo is None:
        current_exp = current_exp.replace(tzinfo=timezone.utc)
    base = max(current_exp, datetime.now(timezone.utc))
    new_expires = (base + timedelta(days=pkg["duration_days"])).isoformat()

    try:
        update_user(sub["panel_user_id"], expireAt=new_expires)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("message", e.response.text)
        except Exception:
            detail = e.response.text
        raise APIError(f"Panel error: {detail}", 502)

    update_expires_at(sub_id, new_expires)
    update_balance(client_token["id"], -pkg["price"])

    create_transaction(
        client_token_id=client_token["id"],
        amount=-pkg["price"],
        tx_type="charge",
        description=f"Renew: {pkg['name']}",
        subscription_id=sub_id,
    )

    sub["expires_at"] = new_expires
    sub["status"] = "active"
    return sub


def delete_sub(sub_id, client_token_id):
    sub = get_subscription_for_client(sub_id, client_token_id)
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Already deleted", 400)

    try:
        delete_user(sub["panel_user_id"])
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise

    mark_deleted(sub_id)
    sub["status"] = "deleted"
    return sub
