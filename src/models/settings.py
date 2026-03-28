from src.database import get_db
from src.config import EXTRA_DEVICE_SURCHARGE_PCT as _DEFAULT_SURCHARGE, BASE_DEVICES_INCLUDED as _DEFAULT_BASE_DEVICES


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()


def get_extra_device_surcharge_pct() -> float:
    val = get_setting("extra_device_surcharge_pct")
    return float(val) if val is not None else _DEFAULT_SURCHARGE


def get_base_devices_included() -> int:
    val = get_setting("base_devices_included")
    return int(val) if val is not None else _DEFAULT_BASE_DEVICES
