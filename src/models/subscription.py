from src.database import get_db


def create_subscription(client_token_id, package_id, panel_user_id, panel_subscription_token, expires_at):
    db = get_db()
    cur = db.execute(
        "INSERT INTO subscriptions (client_token_id, package_id, panel_user_id, panel_subscription_token, expires_at) VALUES (?,?,?,?,?)",
        (client_token_id, package_id, panel_user_id, panel_subscription_token, expires_at),
    )
    db.commit()
    return get_subscription(cur.lastrowid)


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
