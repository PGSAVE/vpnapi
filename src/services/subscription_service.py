import logging
import math
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

import httpx

from src.config import PANEL_SUB_URL, TELEGRAM_BOT_TOKEN
from src.models.settings import get_extra_device_surcharge_pct, get_base_devices_included
from src.models.package import get_package
from src.models.client_token import get_client_token_by_id, update_balance
from src.models.subscription import (
    create_subscription,
    get_subscription,
    get_subscription_for_client,
    mark_deleted,
    set_migrated_to,
    update_expires_at,
)
from src.models.transaction import create_transaction, get_subscription_charges
from src.services.panel_api import (
    create_user,
    delete_user,
    update_user,
    get_hwid_devices,
    delete_hwid_device,
    delete_all_hwid_devices,
)

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
        body = e.response.json()
        return body.get("message") or body.get("error") or e.response.text
    except Exception:
        return e.response.text


def _iso_z(dt: datetime) -> str:
    """Serialize a datetime to ISO-8601 with a trailing Z (Remnawave format)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _make_username(user_id=None) -> str:
    """Build a Remnawave-safe username (6-34 chars, [A-Za-z0-9_-])."""
    suffix = secrets.token_hex(4)
    base = f"api_{user_id}_{suffix}" if user_id else f"api_{suffix}_{secrets.token_hex(4)}"
    base = re.sub(r"[^A-Za-z0-9_-]", "", base)[:34]
    if len(base) < 6:
        base = (base + secrets.token_hex(4))[:34]
    return base


def sub_link(sub: dict) -> str | None:
    """Resolve a subscription's public link for any panel."""
    if sub.get("sub_url"):
        return sub["sub_url"]
    if sub.get("panel_subscription_token"):
        # Legacy Celerity link format.
        return f"{PANEL_SUB_URL}/api/files/{sub['panel_subscription_token']}"
    return None


def _is_legacy(sub: dict) -> bool:
    return sub.get("panel") == "celerity"


def _require_remnawave(sub: dict):
    if _is_legacy(sub) or not sub.get("panel_uuid"):
        raise APIError(
            "Операция недоступна для старой подписки. Замените ссылку на новую (миграция).",
            400,
        )


def _panel_call(fn, *args, action: str, **kwargs):
    """Invoke a panel API call, translating httpx errors into APIError."""
    try:
        return fn(*args, **kwargs)
    except httpx.HTTPStatusError as e:
        detail = _parse_panel_error(e)
        logger.error("Panel error on %s: %s %s", action, e.response.status_code, detail)
        raise APIError(f"Panel error: {detail}", 502)
    except httpx.ConnectError:
        logger.error("Panel unreachable on %s", action)
        raise APIError("Panel unreachable", 502)


def _calc_device_surcharge(base_price, devices):
    """Calculate the surcharge for extra devices beyond base included.

    Returns (surcharge_amount, extra_device_count).
    If devices <= base_included or devices == 0, surcharge is 0.
    """
    base_included = get_base_devices_included()
    surcharge_pct = get_extra_device_surcharge_pct()
    if not devices or devices <= base_included:
        return 0.0, 0
    extra = devices - base_included
    surcharge = base_price * (surcharge_pct / 100) * extra
    return surcharge, extra


def create_sub(client_token, package_id, user_id=None, days=None, devices=None):
    pkg = get_package(package_id)
    if not pkg or not pkg["active"]:
        raise APIError("Package not found", 404)

    is_flex = not pkg["duration_days"]
    if is_flex:
        if not days or days <= 0:
            raise APIError("Parameter 'days' is required for flexible packages", 400)
        duration = days
        base_price = pkg["price"] * days
    else:
        duration = pkg["duration_days"]
        base_price = pkg["price"]

    surcharge, extra_devs = _calc_device_surcharge(pkg["price"], devices)
    if is_flex and extra_devs:
        surcharge = pkg["price"] * (get_extra_device_surcharge_pct() / 100) * extra_devs * days
    price = base_price + surcharge

    if client_token["balance"] < price:
        _notify_insufficient_balance(client_token.get("telegram_user_id"), client_token["balance"], price)
        raise APIError("Insufficient balance", 402)

    username = _make_username(user_id)
    expires_at = _iso_z(datetime.now(timezone.utc) + timedelta(days=duration))

    # Device limit sent to the panel: explicit count, or package default.
    hwid_limit = devices if devices and devices > 0 else (pkg.get("max_devices") or None)

    panel_user = _panel_call(
        create_user,
        username=username,
        squads=pkg["groups"],
        traffic_limit_gb=pkg["traffic_limit_gb"],
        expire_at=expires_at,
        hwid_device_limit=hwid_limit,
        action="create_user",
    )

    new_balance = update_balance(client_token["id"], -price)
    if new_balance < LOW_BALANCE_THRESHOLD:
        _notify_low_balance(client_token.get("telegram_user_id"), new_balance)

    sub = create_subscription(
        client_token_id=client_token["id"],
        package_id=pkg["id"],
        panel_user_id=panel_user["username"],
        panel_subscription_token=panel_user.get("shortUuid"),
        expires_at=expires_at,
        panel="remnawave",
        panel_uuid=panel_user["uuid"],
        sub_url=panel_user.get("subscriptionUrl"),
    )

    desc = f"Subscription: {pkg['name']}" + (f" ({duration}д)" if is_flex else "")
    if extra_devs:
        desc += f" +{extra_devs} устр."
    create_transaction(
        client_token_id=client_token["id"],
        amount=-price,
        tx_type="charge",
        description=desc,
        subscription_id=sub["id"],
    )

    logger.info("Subscription created: id=%s client=%s pkg=%s days=%s devices=%s price=%.0f surcharge=%.0f balance=%.0f", sub["id"], client_token["id"], pkg["name"], duration, devices, price, surcharge, new_balance)

    return {**sub, "sub_link": sub_link(sub)}


def renew_sub(client_token, sub_id, days=None):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Cannot renew deleted subscription", 400)
    _require_remnawave(sub)

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

    base = max(_parse_dt(sub["expires_at"]), datetime.now(timezone.utc))
    new_expires = _iso_z(base + timedelta(days=duration))

    _panel_call(update_user, sub["panel_uuid"], expireAt=new_expires, action="renew update_user")

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


def update_sub_devices(client_token, sub_id, devices):
    """Change device limit (hwidDeviceLimit) on an existing subscription.

    ``devices`` = 0 means reset to the panel/global default.
    Extra devices (above BASE_DEVICES_INCLUDED) incur a one-time surcharge
    based on remaining days.
    """
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Cannot modify deleted subscription", 400)
    _require_remnawave(sub)

    if devices < 0:
        raise APIError("devices must be >= 0", 400)

    pkg = get_package(sub["package_id"])
    if not pkg:
        raise APIError("Package not found", 404)

    # Calculate remaining days for surcharge
    remaining = (_parse_dt(sub["expires_at"]) - datetime.now(timezone.utc)).total_seconds() / 86400
    remaining_days = max(remaining, 0)

    # Surcharge for extra devices
    surcharge = 0.0
    extra_devs = 0
    base_included = get_base_devices_included()
    if devices > base_included:
        extra_devs = devices - base_included
        surcharge = pkg["price"] * (get_extra_device_surcharge_pct() / 100) * extra_devs * remaining_days

    if surcharge > 0:
        if client_token["balance"] < surcharge:
            _notify_insufficient_balance(client_token.get("telegram_user_id"), client_token["balance"], surcharge)
            raise APIError("Insufficient balance", 402)

    # 0 resets to the panel/global default device limit.
    _panel_call(update_user, sub["panel_uuid"], hwidDeviceLimit=devices, action="update_user devices")

    if surcharge > 0:
        new_balance = update_balance(client_token["id"], -surcharge)
        if new_balance < LOW_BALANCE_THRESHOLD:
            _notify_low_balance(client_token.get("telegram_user_id"), new_balance)

        desc = f"Devices: {pkg['name']} +{extra_devs} устр. ({remaining_days:.0f}д)"
        create_transaction(
            client_token_id=client_token["id"],
            amount=-surcharge,
            tx_type="charge",
            description=desc,
            subscription_id=sub_id,
        )
        logger.info("Devices updated: sub=%s devices=%s surcharge=%.0f balance=%.0f", sub_id, devices, surcharge, new_balance)
    else:
        logger.info("Devices updated: sub=%s devices=%s (no surcharge)", sub_id, devices)

    return {"success": True, "devices": devices, "surcharge": round(surcharge, 2)}


# ---------------------------------------------------------------------------
# Migration: replace a legacy Celerity link with a fresh Remnawave one (1:1)
# ---------------------------------------------------------------------------


def migrate_sub(client_token, sub_id):
    """Replace a legacy Celerity subscription with an equivalent Remnawave one.

    Same expiry, same package conditions (traffic, squads, devices). The old
    subscription is kept (not deleted). No balance is charged. Idempotent: if
    the subscription was already migrated, the existing replacement is returned.
    """
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)

    if not _is_legacy(sub):
        raise APIError("Подписка уже на новой панели (Remnawave)", 400)

    # Already migrated → remind of the existing replacement, do not create again.
    if sub.get("migrated_to_id"):
        new = get_subscription(sub["migrated_to_id"])
        if new:
            return {**new, "sub_link": sub_link(new), "already_migrated": True}

    if sub["status"] == "deleted":
        raise APIError("Cannot migrate a deleted subscription", 400)

    pkg = get_package(sub["package_id"])
    if not pkg:
        raise APIError("Package not found", 404)

    # Preserve the original term exactly.
    expires_at = _iso_z(_parse_dt(sub["expires_at"]))
    username = _make_username()
    hwid_limit = pkg.get("max_devices") or None

    panel_user = _panel_call(
        create_user,
        username=username,
        squads=pkg["groups"],
        traffic_limit_gb=pkg["traffic_limit_gb"],
        expire_at=expires_at,
        hwid_device_limit=hwid_limit,
        description=f"migrated from celerity sub#{sub_id}",
        action="migrate create_user",
    )

    new = create_subscription(
        client_token_id=client_token["id"],
        package_id=pkg["id"],
        panel_user_id=panel_user["username"],
        panel_subscription_token=panel_user.get("shortUuid"),
        expires_at=expires_at,
        panel="remnawave",
        panel_uuid=panel_user["uuid"],
        sub_url=panel_user.get("subscriptionUrl"),
    )
    set_migrated_to(sub_id, new["id"])

    logger.info("Subscription migrated: old=%s -> new=%s client=%s pkg=%s", sub_id, new["id"], client_token["id"], pkg["name"])

    return {**new, "sub_link": sub_link(new), "already_migrated": False}


# ---------------------------------------------------------------------------
# HWID device management
# ---------------------------------------------------------------------------


def list_sub_devices(client_token, sub_id):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    _require_remnawave(sub)
    devices = _panel_call(get_hwid_devices, sub["panel_uuid"], action="get_hwid_devices")
    return {"devices": devices, "total": len(devices)}


def reset_sub_devices(client_token, sub_id):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    _require_remnawave(sub)
    _panel_call(delete_all_hwid_devices, sub["panel_uuid"], action="delete_all_hwid_devices")
    logger.info("HWID reset: sub=%s client=%s", sub_id, client_token["id"])
    return {"success": True}


def delete_sub_device(client_token, sub_id, hwid):
    sub = get_subscription_for_client(sub_id, client_token["id"])
    if not sub:
        raise APIError("Subscription not found", 404)
    _require_remnawave(sub)
    _panel_call(delete_hwid_device, sub["panel_uuid"], hwid, action="delete_hwid_device")
    logger.info("HWID device removed: sub=%s hwid=%s", sub_id, hwid)
    return {"success": True}


def calc_refund(sub):
    """Calculate refund amount for unused days of a subscription.

    Returns 0 if no refund is applicable:
    - subscription is not active or already expired
    - less than 7 days remaining
    - more than 15% of total duration has been used
    """
    if sub["status"] != "active":
        return 0

    now = datetime.now(timezone.utc)
    created = _parse_dt(sub["created_at"])
    expires = _parse_dt(sub["expires_at"])

    if expires <= now:
        return 0

    total_seconds = (expires - created).total_seconds()
    if total_seconds <= 0:
        return 0

    remaining_seconds = (expires - now).total_seconds()
    used_seconds = (now - created).total_seconds()

    remaining_days = remaining_seconds / 86400
    total_days = total_seconds / 86400
    used_days = used_seconds / 86400

    if remaining_days < 7:
        return 0

    if used_days / total_days > 0.15:
        return 0

    total_paid = get_subscription_charges(sub["id"])
    if total_paid <= 0:
        return 0

    refund = math.floor(total_paid * remaining_days / total_days)
    return max(refund, 0)


def delete_sub(sub_id, client_token_id):
    sub = get_subscription_for_client(sub_id, client_token_id)
    if not sub:
        raise APIError("Subscription not found", 404)
    if sub["status"] == "deleted":
        raise APIError("Already deleted", 400)

    refund = calc_refund(sub)

    # Legacy Celerity panel is decommissioned — there is no panel user to delete,
    # so legacy subscriptions are removed locally only.
    if not _is_legacy(sub) and sub.get("panel_uuid"):
        try:
            delete_user(sub["panel_uuid"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                detail = _parse_panel_error(e)
                logger.error("Panel error on delete_user: %s %s", e.response.status_code, detail)
                raise APIError(f"Panel error: {detail}", 502)
        except httpx.ConnectError:
            logger.error("Panel unreachable on delete_user")
            raise APIError("Panel unreachable", 502)

    if refund > 0:
        update_balance(client_token_id, refund)
        create_transaction(
            client_token_id=client_token_id,
            amount=refund,
            tx_type="refund",
            description=f"Возврат: подписка #{sub_id} ({refund}₽)",
            subscription_id=sub_id,
        )

    mark_deleted(sub_id)
    logger.info("Subscription deleted: id=%s client=%s refund=%s", sub_id, client_token_id, refund)
    sub["status"] = "deleted"
    sub["refund"] = refund
    return sub
