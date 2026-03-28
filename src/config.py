import os
from dotenv import load_dotenv

load_dotenv()

PANEL_API_URL = os.getenv("PANEL_API_URL", "http://localhost:3000")
PANEL_API_KEY = os.getenv("PANEL_API_KEY", "")
PORT = int(os.getenv("PORT", "3100"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "")
PANEL_SUB_URL = os.getenv("PANEL_SUB_URL", PANEL_API_URL)
DATABASE_PATH = os.getenv("DATABASE_PATH", "data.db")
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")
DOCS_URL = os.getenv("DOCS_URL", "")
DOCS_PASS = os.getenv("DOCS_PASS", "")
EXTRA_DEVICE_SURCHARGE_PCT = float(os.getenv("EXTRA_DEVICE_SURCHARGE_PCT", "50"))
BASE_DEVICES_INCLUDED = int(os.getenv("BASE_DEVICES_INCLUDED", "3"))
