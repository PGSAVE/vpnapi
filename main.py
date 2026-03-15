import asyncio
import threading
import uvicorn
from fastapi import FastAPI

from src.config import PORT, TELEGRAM_BOT_TOKEN
from src.database import init_db
from src.api.routes import router

app = FastAPI(title="Celerity Wrapper")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


def run_bot():
    from telegram.ext import ApplicationBuilder
    from src.bot import admin_handlers, client_handlers

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    admin_handlers.register(application)
    client_handlers.register(application)

    print("Telegram bot started")
    application.run_polling(drop_pending_updates=True)


def main():
    init_db()
    print("Database initialized")

    if TELEGRAM_BOT_TOKEN:
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
    else:
        print("TELEGRAM_BOT_TOKEN not set, bot disabled")

    print(f"Celerity Wrapper API listening on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
