import httpx
from src.config import PANEL_API_URL, PANEL_API_KEY

_client = httpx.Client(
    base_url=PANEL_API_URL,
    headers={"X-API-Key": PANEL_API_KEY},
    timeout=15.0,
)


def create_user(user_id: str, username: str, groups: list, traffic_limit_gb: float, expire_at: str) -> dict:
    r = _client.post("/api/users", json={
        "userId": user_id,
        "username": username,
        "groups": groups,
        "enabled": True,
        "trafficLimit": int(traffic_limit_gb * 1024 * 1024 * 1024),
        "expireAt": expire_at,
    })
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
