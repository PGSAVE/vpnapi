import asyncio
import logging
import threading

import uvicorn
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.api.routes import router
from src.config import DOCS_PASS, DOCS_URL, PORT, TELEGRAM_BOT_TOKEN
from src.database import init_db

docs_path = f"/{DOCS_URL}" if DOCS_URL else "/docs"
app = FastAPI(
    title="tgVPNapi",
    description="API для управления VPN-подписками. Авторизация через Bearer-токен.",
    version="1.0.0",
    docs_url=docs_path,
    redoc_url=None,
)
app.include_router(router)


class DocsBasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if DOCS_URL and DOCS_PASS and request.url.path in (
            f"/{DOCS_URL}", f"/{DOCS_URL}/oauth2-redirect", "/openapi.json"
        ):
            import base64
            auth = request.headers.get("authorization", "")
            if auth.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth[6:]).decode()
                    _, password = decoded.split(":", 1)
                    if password == DOCS_PASS:
                        return await call_next(request)
                except Exception:
                    pass
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="docs"'},
            )
        return await call_next(request)


class SilentDropMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if response.status_code == 404:
            return Response(status_code=444)
        return response


app.add_middleware(SilentDropMiddleware)
app.add_middleware(DocsBasicAuthMiddleware)


def run_bot():
    from telegram.ext import ApplicationBuilder

    from src.bot import admin_handlers, client_handlers

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    admin_handlers.register(application)
    client_handlers.register(application)

    print("Telegram bot started")

    async def _run():
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        # Keep alive until the daemon thread is torn down on process exit
        await asyncio.Event().wait()

    loop.run_until_complete(_run())


def main():
    init_db()
    print("Database initialized")

    if TELEGRAM_BOT_TOKEN:
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
    else:
        print("TELEGRAM_BOT_TOKEN not set, bot disabled")

    print(f"tgVPNapi listening on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
