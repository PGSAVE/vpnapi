from src.database import get_db


def create_subscription(
    client_token_id,
    package_id,
    panel_user_id,
    panel_subscription_token,
    expires_at,
    panel="remnawave",
    panel_uuid=None,
    sub_url=None,
):
    db = get_db()
    cur = db.execute(
        "INSERT INTO subscriptions "
        "(client_token_id, package_id, panel_user_id, panel_subscription_token, panel, panel_uuid, sub_url, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (client_token_id, package_id, panel_user_id, panel_subscription_token, panel, panel_uuid, sub_url, expires_at),
    )
    db.commit()
    return get_subscription(cur.lastrowid)


def set_migrated_to(old_sub_id, new_sub_id):
    """Link a legacy (Celerity) subscription to its new Remnawave replacement."""
    db = get_db()
    db.execute("UPDATE subscriptions SET migrated_to_id=? WHERE id=?", (new_sub_id, old_sub_id))
    db.commit()


def get_subscription(sub_id):
    row = get_db().execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return dict(row) if row else None


def get_subscription_for_client(sub_id, client_token_id):
    row = get_db().execute("SELECT * FROM subscriptions WHERE id=? AND client_token_id=?", (sub_id, client_token_id)).fetchone()
    return dict(row) if row else None


def list_subscriptions(client_token_id, include_deleted=False):
    q = "SELECT s.*, p.name as package_name, p.traffic_limit_gb, p.duration_days FROM subscriptions s JOIN packages p ON s.package_id=p.id WHERE s.client_token_id=?"
    if not include_deleted:
        q += " AND s.status != 'deleted'"
    q += " ORDER BY s.created_at DESC"
    return [dict(r) for r in get_db().execute(q, (client_token_id,)).fetchall()]


def mark_deleted(sub_id):
    db = get_db()
    db.execute("UPDATE subscriptions SET status='deleted' WHERE id=?", (sub_id,))
    db.commit()


def update_expires_at(sub_id, new_expires_at):
    db = get_db()
    db.execute("UPDATE subscriptions SET expires_at=?, status='active' WHERE id=?", (new_expires_at, sub_id))
    db.commit()


def count_subscriptions(client_token_id=None, status=None):
    q = "SELECT COUNT(*) as cnt FROM subscriptions WHERE 1=1"
    params = []
    if client_token_id is not None:
        q += " AND client_token_id=?"
        params.append(client_token_id)
    if status is not None:
        q += " AND status=?"
        params.append(status)
    return get_db().execute(q, params).fetchone()["cnt"]


def count_expired():
    """Count active subscriptions past their expiry date."""
    return get_db().execute(
        "SELECT COUNT(*) as cnt FROM subscriptions WHERE status='active' AND expires_at < datetime('now')"
    ).fetchone()["cnt"]


def count_new_subscriptions_today():
    return get_db().execute(
        "SELECT COUNT(*) as cnt FROM subscriptions WHERE date(created_at)=date('now')"
    ).fetchone()["cnt"]


def search_subscriptions(client_token_id, query):
    """Search subscriptions by panel_user_id or package name (partial match)."""
    q = (
        "SELECT s.*, p.name as package_name, p.traffic_limit_gb, p.duration_days "
        "FROM subscriptions s JOIN packages p ON s.package_id=p.id "
        "WHERE s.client_token_id=? AND (s.panel_user_id LIKE ? OR p.name LIKE ?) "
        "ORDER BY s.created_at DESC LIMIT 20"
    )
    like = f"%{query}%"
    return [dict(r) for r in get_db().execute(q, (client_token_id, like, like)).fetchall()]


def list_subscriptions_page(client_token_id, offset=0, limit=10, status_filter=None):
    """Paginated subscription list."""
    q = (
        "SELECT s.*, p.name as package_name, p.traffic_limit_gb, p.duration_days "
        "FROM subscriptions s JOIN packages p ON s.package_id=p.id "
        "WHERE s.client_token_id=?"
    )
    params: list = [client_token_id]
    if status_filter:
        q += " AND s.status=?"
        params.append(status_filter)
    else:
        q += " AND s.status != 'deleted'"
    q += " ORDER BY s.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(r) for r in get_db().execute(q, params).fetchall()]


def get_client_stats(client_token_id):
    """Get aggregated stats for a client."""
    db = get_db()
    row = db.execute(
        "SELECT "
        "  COUNT(*) as total, "
        "  COUNT(CASE WHEN status='active' THEN 1 END) as active, "
        "  COUNT(CASE WHEN status='expired' THEN 1 END) as expired, "
        "  COUNT(CASE WHEN status='deleted' THEN 1 END) as deleted, "
        "  COUNT(CASE WHEN status='active' AND expires_at < datetime('now') THEN 1 END) as overdue "
        "FROM subscriptions WHERE client_token_id=?",
        (client_token_id,),
    ).fetchone()
    tx = db.execute(
        "SELECT "
        "  SUM(CASE WHEN type='topup' THEN amount ELSE 0 END) as topups, "
        "  SUM(CASE WHEN type='charge' THEN ABS(amount) ELSE 0 END) as spent, "
        "  COUNT(CASE WHEN type='charge' THEN 1 END) as purchases "
        "FROM transactions WHERE client_token_id=?",
        (client_token_id,),
    ).fetchone()
    return {
        "total": row["total"],
        "active": row["active"],
        "expired": row["expired"],
        "deleted": row["deleted"],
        "overdue": row["overdue"],
        "topups": tx["topups"] or 0,
        "spent": tx["spent"] or 0,
        "purchases": tx["purchases"] or 0,
    }
