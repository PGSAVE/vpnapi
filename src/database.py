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
            panel TEXT NOT NULL DEFAULT 'remnawave',
            panel_uuid TEXT,
            sub_url TEXT,
            migrated_to_id INTEGER REFERENCES subscriptions(id),
            status TEXT DEFAULT 'active' CHECK(status IN ('active','expired','deleted')),
            expires_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
    _migrate_to_remnawave(db)


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_to_remnawave(db: sqlite3.Connection):
    """Migrate the schema/data from the Celerity layout to Remnawave.

    Idempotent: safe to run on every startup.
      1. Add the new subscription columns to legacy databases.
      2. Tag every pre-existing subscription as panel='celerity'.
      3. Assign the default 'main' Remnawave squad to every package (one-time).
    """
    # 1. Add new columns to legacy `subscriptions` tables.
    cols = _column_names(db, "subscriptions")
    alters = {
        "panel": "ALTER TABLE subscriptions ADD COLUMN panel TEXT",
        "panel_uuid": "ALTER TABLE subscriptions ADD COLUMN panel_uuid TEXT",
        "sub_url": "ALTER TABLE subscriptions ADD COLUMN sub_url TEXT",
        "migrated_to_id": "ALTER TABLE subscriptions ADD COLUMN migrated_to_id INTEGER",
    }
    for col, stmt in alters.items():
        if col not in cols:
            db.execute(stmt)

    # 2. Every subscription that predates this migration was created on Celerity.
    #    New rows are inserted with panel='remnawave' explicitly, so any NULL is
    #    by definition a legacy row. Backfill unconditionally (idempotent) so a
    #    partially-migrated database never leaves rows in limbo.
    cur = db.execute("UPDATE subscriptions SET panel='celerity' WHERE panel IS NULL")
    if cur.rowcount:
        log.info("Tagged %d existing subscriptions as Celerity (legacy)", cur.rowcount)
    db.commit()

    # 3. Assign the default main squad to all packages (one-time).
    _assign_default_squad(db)


def _assign_default_squad(db: sqlite3.Connection):
    """One-time: set the default Remnawave 'main' squad on every package."""
    from src.models.settings import get_setting, set_setting

    if get_setting("remnawave_squad_migration_done") == "1":
        return

    from src.config import DEFAULT_SQUAD_UUID, DEFAULT_SQUAD_NAME
    from src.services.panel_api import get_main_squad

    if DEFAULT_SQUAD_UUID:
        squad = {"uuid": DEFAULT_SQUAD_UUID, "name": DEFAULT_SQUAD_NAME}
    else:
        squad = get_main_squad()

    if not squad:
        log.warning(
            "Default squad not resolved (panel unreachable and DEFAULT_SQUAD_UUID unset); "
            "will retry on next startup"
        )
        return

    payload = json.dumps([squad])
    rows = db.execute("SELECT id FROM packages").fetchall()
    for row in rows:
        db.execute("UPDATE packages SET groups=? WHERE id=?", (payload, row["id"]))
    db.commit()
    set_setting("remnawave_squad_migration_done", "1")
    log.info("Assigned default squad '%s' to %d packages", squad["name"], len(rows))
