import httpx

from src.config import PANEL_API_KEY, PANEL_API_URL

_client = httpx.Client(
    base_url=PANEL_API_URL,
    headers={"X-API-Key": PANEL_API_KEY},
    timeout=15.0,
)


def create_user(
    user_id: str, username: str, groups: list, traffic_limit_gb: float, expire_at: str
) -> dict:
    # groups may be list of dicts {_id, name} — panel expects ObjectId strings
    group_ids = [g["_id"] if isinstance(g, dict) else g for g in groups]
    r = _client.post(
        "/api/users",
        json={
            "userId": user_id,
            "username": username,
            "groups": group_ids,
            "enabled": True,
            "trafficLimit": int(traffic_limit_gb * 1024 * 1024 * 1024),
            "expireAt": expire_at,
        },
    )
    r.raise_for_status()
    return r.json()


def get_user(user_id: str) -> dict:
    r = _client.get(f"/api/users/{user_id}")
    r.raise_for_status()
    return r.json()


def delete_user(user_id: str):
    r = _client.delete(f"/api/users/{user_id}")
    r.raise_for_status()
    return r.json()


def get_stats() -> dict:
    r = _client.get("/api/stats")
    r.raise_for_status()
    return r.json()


def list_groups() -> list[dict]:
    """Fetch available groups from the Celerity panel.

    Returns a list of dicts with ``_id`` (ObjectId string) and ``name``.
    Returns an empty list if the panel is unreachable.
    """
    try:
        r = _client.get("/api/groups")
        r.raise_for_status()
        data = r.json()

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("groups") or data.get("data") or data.get("items") or []
        else:
            return []

        result: list[dict] = []
        for item in items:
            if isinstance(item, dict) and item.get("_id"):
                result.append({"_id": str(item["_id"]), "name": str(item.get("name", item["_id"]))})
        return result

    except Exception:
        return []
