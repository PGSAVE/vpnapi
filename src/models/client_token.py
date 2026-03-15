import secrets
from src.database import get_db


def create_client_token(name, telegram_user_id=None):
    token = secrets.token_hex(24)
    db = get_db()
    cur = db.execute(
        "INSERT INTO client_tokens (token, name, telegram_user_id) VALUES (?,?,?)",
        (token, name, telegram_user_id),
    )
    db.commit()
    return get_client_token_by_id(cur.lastrowid)


def get_client_token_by_id(ct_id):
    row = get_db().execute("SELECT * FROM client_tokens WHERE id=?", (ct_id,)).fetchone()
    return dict(row) if row else None


def get_client_token(token):
    row = get_db().execute("SELECT * FROM client_tokens WHERE token=? AND active=1", (token,)).fetchone()
    return dict(row) if row else None


def get_client_token_by_telegram(tg_id):
    row = get_db().execute("SELECT * FROM client_tokens WHERE telegram_user_id=? AND active=1", (str(tg_id),)).fetchone()
    return dict(row) if row else None


def list_client_tokens():
    return [dict(r) for r in get_db().execute("SELECT * FROM client_tokens ORDER BY created_at DESC").fetchall()]


def update_balance(ct_id, delta):
    db = get_db()
    db.execute("UPDATE client_tokens SET balance = balance + ? WHERE id=?", (delta, ct_id))
    db.commit()
    row = db.execute("SELECT balance FROM client_tokens WHERE id=?", (ct_id,)).fetchone()
    return row["balance"] if row else None
