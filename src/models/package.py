import json
from src.database import get_db


def create_package(name, traffic_limit_gb, max_devices, duration_days, price, description="", groups=None):
    db = get_db()
    cur = db.execute(
        "INSERT INTO packages (name, description, traffic_limit_gb, max_devices, duration_days, price, groups) VALUES (?,?,?,?,?,?,?)",
        (name, description, traffic_limit_gb, max_devices, duration_days, price, json.dumps(groups or [])),
    )
    db.commit()
    return get_package(cur.lastrowid)


def get_package(pkg_id):
    row = get_db().execute("SELECT * FROM packages WHERE id=?", (pkg_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_packages(active_only=True):
    q = "SELECT * FROM packages"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY created_at DESC"
    return [_row_to_dict(r) for r in get_db().execute(q).fetchall()]


def update_package(pkg_id, **fields):
    allowed = {"name", "description", "traffic_limit_gb", "max_devices", "duration_days", "price", "active", "groups"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "groups" in updates:
        updates["groups"] = json.dumps(updates["groups"])
    if not updates:
        return
    sets = ", ".join(f"{k}=?" for k in updates)
    get_db().execute(f"UPDATE packages SET {sets} WHERE id=?", (*updates.values(), pkg_id))
    get_db().commit()
    return get_package(pkg_id)


def _row_to_dict(row):
    d = dict(row)
    d["groups"] = json.loads(d.get("groups") or "[]")
    return d
