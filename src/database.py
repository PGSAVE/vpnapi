import atexit
import json
import logging
import sqlite3
import threading
from src.config import DATABASE_PATH

log = logging.getLogger(__name__)

_local = threading.local()
_all_connections: list[sqlite3.Connection] = []
_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        with _lock:
            _all_connections.append(conn)
    return conn


def close_all():
    """Close all connections and checkpoint WAL into the main database file."""
    with _lock:
        for conn in _all_connections:
            try:
                conn.close()
            except Exception:
                pass
        _all_connections.clear()
    # Final checkpoint with a fresh connection
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        log.info("WAL checkpoint completed")
    except Exception:
        log.warning("WAL checkpoint failed", exc_info=True)


atexit.register(close_all)

_checkpoint_timer = None


def _periodic_checkpoint():
    """Run WAL checkpoint every 5 minutes."""
    global _checkpoint_timer
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        conn.close()
        log.debug("Periodic WAL checkpoint done")
    except Exception:
        log.warning("Periodic WAL checkpoint failed", exc_info=True)
    _checkpoint_timer = threading.Timer(300, _periodic_checkpoint)
    _checkpoint_timer.daemon = True
    _checkpoint_timer.start()


def start_periodic_checkpoint():
    _periodic_checkpoint()


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            traffic_limit_gb REAL NOT NULL,
            max_devices INTEGER DEFAULT 1,
            duration_days INTEGER NOT NULL,
            price REAL NOT NULL,
            groups TEXT DEFAULT '[]',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS client_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            telegram_user_id TEXT,
            balance REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_token_id INTEGER NOT NULL REFERENCES client_tokens(id),
            package_id INTEGER NOT NULL REFERENCES packages(id),
            panel_user_id TEXT NOT NULL,
            panel_subscription_token TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN ('active','expired','deleted')),
            expires_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_token_id INTEGER NOT NULL REFERENCES client_tokens(id),
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('topup','charge','refund')),
            description TEXT DEFAULT '',
            subscription_id INTEGER REFERENCES subscriptions(id),
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    _migrate_group_names_to_ids(db)


def _migrate_group_names_to_ids(db: sqlite3.Connection):
    """One-time migration: convert group name strings to {_id, name} dicts."""
    from src.services.panel_api import list_groups

    rows = db.execute("SELECT id, groups FROM packages").fetchall()
    needs_migration = False
    for row in rows:
        groups = json.loads(row["groups"] or "[]")
        if groups and isinstance(groups[0], str):
            needs_migration = True
            break

    if not needs_migration:
        return

    panel_groups = list_groups()
    name_to_group = {g["name"]: g for g in panel_groups}

    updated = 0
    for row in rows:
        groups = json.loads(row["groups"] or "[]")
        if not groups or not isinstance(groups[0], str):
            continue
        new_groups = []
        for name in groups:
            if name in name_to_group:
                new_groups.append(name_to_group[name])
            else:
                log.warning("Group '%s' not found in panel, skipping (pkg %s)", name, row["id"])
        db.execute("UPDATE packages SET groups=? WHERE id=?", (json.dumps(new_groups), row["id"]))
        updated += 1

    if updated:
        db.commit()
        log.info("Migrated groups for %d packages (name -> ObjectId)", updated)
