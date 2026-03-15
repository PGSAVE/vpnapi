import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

import httpx

from src.config import PANEL_SUB_URL, TELEGRAM_BOT_TOKEN
from src.models.package import get_package
from src.models.client_token import get_client_token_by_id, update_balance
from src.models.subscription import create_subscription, get_subscription_for_client, mark_deleted, update_expires_at
from src.models.transaction import create_transaction
from src.services.panel_api import create_user, delete_user, update_user

logger = logging.getLogger(__name__)

LOW_BALANCE_THRESHOLD = 1000


def _send_telegram_message(telegram_user_id: str, text: str):
    """Send a Telegram message in a background thread."""
    if not TELEGRAM_BOT_TOKEN or not telegram_user_id:
        return

    def _send():
        try:
            import asyncio
            from telegram import Bot
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            asyncio.run(bot.send_message(chat_id=int(telegram_user_id), text=text))
        except Exception:
            logger.warning("Failed to send Telegram notification to %s", telegram_user_id, exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


def _notify_low_balance(telegram_user_id: str, new_balance: float):
    logger.info("Low balance alert: user_tg=%s balance=%.0f", telegram_user_id, new_balance)
    _send_telegram_message(
        telegram_user_id,
        f"⚠️ Ваш баланс: {new_balance:.0f}₽\n\nПополните баланс для продолжения работы.",
    )


def _notify_insufficient_balance(telegram_user_id: str, balance: float, required: float):
    logger.warning("Insufficient balance: user_tg=%s balance=%.0f required=%.0f", telegram_user_id, balance, required)
    _send_telegram_message(
        telegram_user_id,
        f"❌ Недостаточно средств!\n\nБаланс: {balance:.0f}₽\nТребуется: {required:.0f}₽\n\nПополните баланс для продолжения.",
    )


class APIError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _parse_panel_error(e: httpx.HTTPStatusError) -> str:
    try:
        return e.response.json().get("message", e.response.text)
    except Exception:
        return e.response.text


def create_sub(client_token, package_id, user_id=None, days=None):
    pkg = get_package(package_id)
    if not pkg or not pkg["active"]:
        raise APIError("Package not found", 404)

    is_flex = not pkg["duration_days"]
    if is_flex:
        if not days or days <= 0:
            raise APIError("Parameter 'days' is required for flexible packages", 400)
        duration = days
        price = pkg["price"] * days
    else:
        duration = pkg["duration_days"]
        price = pkg["price"]

    if client_token["balance"] < price:
        _notify_insufficient_balance(client_token.get("telegram_user_id"), client_token["balance"], price)
        raise APIError("Insufficient balance", 402)

    panel_user_id = f"api_{user_id}_{secrets.token_hex(4)}" if user_id else f"api_{secrets.token_hex(4)}_{secrets.token_hex(4)}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=duration)).isoformat()

    try:
        panel_user = create_user(
            user_id=panel_user_id,
            username=panel_user_id,
            groups=pkg["groups"],
            traffic_limit_gb=pkg["traffic_limit_gb"],
            expire_at=expires_at,
        )
    except httpx.HTTPStatusError as e:
        detail = _parse_panel_error(e)
        logger.error("Panel error on create_user: %s %s", e.response.status_code, detail)
        raise APIError(f"Panel error: {detail}", 502)
    except httpx.ConnectError:
        logger.error("Panel unreachable on create_user")
        raise APIError("Panel unreachable", 502)

    new_balance = update_balance(client_token["id"], -price)
    if new_balance < LOW_BALANCE_THRESHOLD:
        _notify_low_balance(client_token.get("telegram_user_id"), new_balance)

    sub = create_subscription(
        client_token_id=client_token["id"],
        package_id=pkg["id"],
        panel_user_id=panel_user["userId"],
        panel_subscription_token=panel_user.get("subscriptionToken"),
        expires_at=expires_at,
    )

    desc = f"Subscription: {pkg['name']}" + (f" ({duration}д)" if is_flex else "")
    create_transaction(
        client_token_id=client_token["id"],
        amount=-price,
        tx_type="charge",
        description=desc,
        subscription_id=sub["id"],
    )

    logger.info("Subscription created: id=%s client=%s pkg=%s days=%s price=%.0f balance=%.0f", sub["id"], client_token["id"], pkg["name"], duration, price, new_balance)

    sub_link = f"{PANEL_SUB_URL}/api/files/{panel_user.get('subscriptionToken')}" if panel_user.get("subscriptionToken") else None
    return {**sub, "sub_link": sub_link}


def renew_sub(client_token, sub_id, days=None):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Cannot renew deleted subscription", 400)

    pkg = get_package(sub["package_id"])
    if not pkg:
        raise APIError("Package not found", 404)

    is_flex = not pkg["duration_days"]
    if is_flex:
        if not days or days <= 0:
            raise APIError("Parameter 'days' is required for flexible packages", 400)
        duration = days
        price = pkg["price"] * days
    else:
        duration = pkg["duration_days"]
        price = pkg["price"]

    if client_token["balance"] < price:
        _notify_insufficient_balance(client_token.get("telegram_user_id"), client_token["balance"], price)
        raise APIError("Insufficient balance", 402)

    current_exp = datetime.fromisoformat(sub["expires_at"].replace("Z", "+00:00"))
    if current_exp.tzinfo is None:
        current_exp = current_exp.replace(tzinfo=timezone.utc)
    base = max(current_exp, datetime.now(timezone.utc))
    new_expires = (base + timedelta(days=duration)).isoformat()

    try:
        update_user(sub["panel_user_id"], expireAt=new_expires)
    except httpx.HTTPStatusError as e:
        detail = _parse_panel_error(e)
        logger.error("Panel error on renew update_user: %s %s", e.response.status_code, detail)
        raise APIError(f"Panel error: {detail}", 502)
    except httpx.ConnectError:
        logger.error("Panel unreachable on renew update_user")
        raise APIError("Panel unreachable", 502)

    update_expires_at(sub_id, new_expires)
    new_balance = update_balance(client_token["id"], -price)
    if new_balance < LOW_BALANCE_THRESHOLD:
        _notify_low_balance(client_token.get("telegram_user_id"), new_balance)

    desc = f"Renew: {pkg['name']}" + (f" ({duration}д)" if is_flex else "")
    create_transaction(
        client_token_id=client_token["id"],
        amount=-price,
        tx_type="charge",
        description=desc,
        subscription_id=sub_id,
    )

    logger.info("Subscription renewed: id=%s client=%s pkg=%s days=%s price=%.0f balance=%.0f", sub_id, client_token["id"], pkg["name"], duration, price, new_balance)

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
            detail = _parse_panel_error(e)
            logger.error("Panel error on delete_user: %s %s", e.response.status_code, detail)
            raise APIError(f"Panel error: {detail}", 502)
    except httpx.ConnectError:
        logger.error("Panel unreachable on delete_user")
        raise APIError("Panel unreachable", 502)

    mark_deleted(sub_id)
    logger.info("Subscription deleted: id=%s client=%s", sub_id, client_token_id)
    sub["status"] = "deleted"
    return sub
