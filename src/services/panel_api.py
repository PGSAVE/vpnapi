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
    r = _client.post(
        "/api/users",
        json={
            "userId": user_id,
            "username": username,
            "groups": groups,
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


def list_groups() -> list[str]:
    """Fetch the available groups from the Celerity panel.

    Returns a plain list of group name strings.
    Returns an empty list if the panel is unreachable or the endpoint does
    not exist so that callers can degrade gracefully.
    """
    try:
        r = _client.get("/api/groups")
        r.raise_for_status()
        data = r.json()

        # The panel may return a bare list or a wrapper object.
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Try common wrapper keys in order of likelihood.
            items = data.get("groups") or data.get("data") or data.get("items") or []
        else:
            return []

        # Each item may be a plain string or a dict with a name/id field.
        result: list[str] = []
        for item in items:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                name = (
                    item.get("name")
                    or item.get("id")
                    or item.get("groupName")
                    or str(item)
                )
                result.append(str(name))
        return result

    except Exception:
        return []
