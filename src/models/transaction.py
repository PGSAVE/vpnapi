from src.database import get_db


def create_transaction(client_token_id, amount, tx_type, description="", subscription_id=None):
    db = get_db()
    db.execute(
        "INSERT INTO transactions (client_token_id, amount, type, description, subscription_id) VALUES (?,?,?,?,?)",
        (client_token_id, amount, tx_type, description, subscription_id),
    )
    db.commit()


def get_stats():
    db = get_db()
    rows = db.execute("SELECT type, SUM(amount) as total FROM transactions GROUP BY type").fetchall()
    return {r["type"]: r["total"] for r in rows}
