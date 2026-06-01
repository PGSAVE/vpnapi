import logging

import httpx

from src.config import PANEL_API_TOKEN, PANEL_API_URL

logger = logging.getLogger(__name__)

# Remnawave authenticates with a Bearer API token.
_client = httpx.Client(
    base_url=PANEL_API_URL,
    headers={
        "Authorization": f"Bearer {PANEL_API_TOKEN}",
        "Accept": "application/json",
    },
    timeout=20.0,
)


def _unwrap(payload: dict) -> dict:
    """Remnawave wraps successful payloads in a top-level ``response`` key."""
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


def _squad_ids(squads: list) -> list[str]:
    """Accept a list of squad dicts ({uuid, name}) or bare uuid strings."""
    return [s["uuid"] if isinstance(s, dict) else s for s in squads]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(
    username: str,
    squads: list,
    traffic_limit_gb: float,
    expire_at: str,
    hwid_device_limit: int | None = None,
    description: str = "",
) -> dict:
    """Create a Remnawave user. Returns the user object (uuid, shortUuid, subscriptionUrl...)."""
    squad_ids = _squad_ids(squads)
    body = {
        "username": username,
        "status": "ACTIVE",
        "trafficLimitBytes": int(traffic_limit_gb * 1024 * 1024 * 1024),
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": expire_at,
        "activeInternalSquads": squad_ids,
    }
    if hwid_device_limit and hwid_device_limit > 0:
        body["hwidDeviceLimit"] = hwid_device_limit
    if description:
        body["description"] = description

    logger.info(
        "Creating panel user: %s squads=%s traffic=%.1fGB devices=%s",
        username, squad_ids, traffic_limit_gb, hwid_device_limit,
    )
    r = _client.post("/api/users", json=body)
    r.raise_for_status()
    return _unwrap(r.json())


def update_user(uuid: str, **fields) -> dict:
    """Update a Remnawave user. The uuid is passed in the body (PATCH /api/users)."""
    logger.info("Updating panel user: %s fields=%s", uuid, list(fields.keys()))
    r = _client.patch("/api/users", json={"uuid": uuid, **fields})
    r.raise_for_status()
    return _unwrap(r.json())


def get_user(uuid: str) -> dict:
    r = _client.get(f"/api/users/{uuid}")
    r.raise_for_status()
    return _unwrap(r.json())


def delete_user(uuid: str) -> dict:
    logger.info("Deleting panel user: %s", uuid)
    r = _client.delete(f"/api/users/{uuid}")
    r.raise_for_status()
    return _unwrap(r.json())


# ---------------------------------------------------------------------------
# Internal squads
# ---------------------------------------------------------------------------


def list_squads() -> list[dict]:
    """Fetch available internal squads from the Remnawave panel.

    Returns a list of dicts with ``uuid`` and ``name``.
    Returns an empty list if the panel is unreachable.
    """
    try:
        r = _client.get("/api/internal-squads")
        r.raise_for_status()
        data = _unwrap(r.json())

        if isinstance(data, dict):
            items = data.get("internalSquads") or data.get("squads") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        else:
            return []

        result: list[dict] = []
        for item in items:
            if isinstance(item, dict) and item.get("uuid"):
                result.append({"uuid": str(item["uuid"]), "name": str(item.get("name", item["uuid"]))})
        return result

    except Exception:
        logger.warning("Failed to fetch internal squads from panel", exc_info=True)
        return []


def get_main_squad() -> dict | None:
    """Return the default 'main' squad, or the first squad available.

    Prefers a squad whose name contains 'main'; falls back to the
    lowest viewPosition (the first one returned by the panel).
    """
    squads = list_squads()
    if not squads:
        return None
    for s in squads:
        if "main" in s["name"].lower():
            return s
    return squads[0]


# ---------------------------------------------------------------------------
# HWID device management
# ---------------------------------------------------------------------------


def get_hwid_devices(user_uuid: str) -> list[dict]:
    """List devices (HWID bindings) for a user."""
    r = _client.get(f"/api/hwid/devices/{user_uuid}")
    r.raise_for_status()
    data = _unwrap(r.json())
    if isinstance(data, dict):
        return data.get("devices", [])
    return data if isinstance(data, list) else []


def delete_hwid_device(user_uuid: str, hwid: str) -> dict:
    """Remove a single device binding."""
    logger.info("Deleting HWID device %s for user %s", hwid, user_uuid)
    r = _client.request(
        "DELETE", "/api/hwid/devices/delete", json={"userUuid": user_uuid, "hwid": hwid}
    )
    r.raise_for_status()
    return _unwrap(r.json())


def delete_all_hwid_devices(user_uuid: str) -> dict:
    """Reset (remove) all device bindings for a user."""
    logger.info("Resetting all HWID devices for user %s", user_uuid)
    r = _client.request(
        "DELETE", "/api/hwid/devices/delete-all", json={"userUuid": user_uuid}
    )
    r.raise_for_status()
    return _unwrap(r.json())
