from src.database import get_db


def create_transaction(client_token_id, amount, tx_type, description="", subscription_id=None):
    db = get_db()
    db.execute(
        "INSERT INTO transactions (client_token_id, amount, type, description, subscription_id) VALUES (?,?,?,?,?)",
        (client_token_id, amount, tx_type, description, subscription_id),
    )
    db.commit()


def get_subscription_charges(subscription_id):
    """Return total amount charged for a subscription (positive value)."""
    row = get_db().execute(
        "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM transactions WHERE subscription_id=? AND type='charge'",
        (subscription_id,),
    ).fetchone()
    return row["total"]


def get_stats():
    db = get_db()
    rows = db.execute("SELECT type, SUM(amount) as total FROM transactions GROUP BY type").fetchall()
    return {r["type"]: r["total"] for r in rows}


def get_detailed_stats():
    db = get_db()
    result = {}

    # All-time totals by type
    rows = db.execute("SELECT type, SUM(amount) as total, COUNT(*) as cnt FROM transactions GROUP BY type").fetchall()
    result["by_type"] = {r["type"]: {"total": r["total"] or 0, "count": r["cnt"]} for r in rows}

    # Today
    row = db.execute(
        "SELECT SUM(CASE WHEN type='topup' THEN amount ELSE 0 END) as topups,"
        "       SUM(CASE WHEN type='charge' THEN amount ELSE 0 END) as charges,"
        "       COUNT(CASE WHEN type='charge' THEN 1 END) as sales "
        "FROM transactions WHERE date(created_at)=date('now')"
    ).fetchone()
    result["today"] = {"topups": row["topups"] or 0, "charges": abs(row["charges"] or 0), "sales": row["sales"] or 0}

    # Last 7 days
    row = db.execute(
        "SELECT SUM(CASE WHEN type='topup' THEN amount ELSE 0 END) as topups,"
        "       SUM(CASE WHEN type='charge' THEN amount ELSE 0 END) as charges,"
        "       COUNT(CASE WHEN type='charge' THEN 1 END) as sales "
        "FROM transactions WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()
    result["week"] = {"topups": row["topups"] or 0, "charges": abs(row["charges"] or 0), "sales": row["sales"] or 0}

    # Last 30 days
    row = db.execute(
        "SELECT SUM(CASE WHEN type='topup' THEN amount ELSE 0 END) as topups,"
        "       SUM(CASE WHEN type='charge' THEN amount ELSE 0 END) as charges,"
        "       COUNT(CASE WHEN type='charge' THEN 1 END) as sales "
        "FROM transactions WHERE created_at >= datetime('now','-30 days')"
    ).fetchone()
    result["month"] = {"topups": row["topups"] or 0, "charges": abs(row["charges"] or 0), "sales": row["sales"] or 0}

    # Top packages by sales
    rows = db.execute(
        "SELECT p.name, COUNT(*) as cnt, SUM(ABS(t.amount)) as revenue "
        "FROM transactions t JOIN subscriptions s ON t.subscription_id=s.id "
        "JOIN packages p ON s.package_id=p.id "
        "WHERE t.type='charge' GROUP BY p.id ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    result["top_packages"] = [{"name": r["name"], "count": r["cnt"], "revenue": r["revenue"] or 0} for r in rows]

    return result
