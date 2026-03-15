from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from src.models.client_token import get_client_token_by_telegram
from src.models.subscription import list_subscriptions, count_subscriptions


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    ct = get_client_token_by_telegram(tg_id)

    if not ct:
        return await update.message.reply_text(
            "Привет! Ваш Telegram не привязан к токену.\n"
            "Обратитесь к администратору для получения доступа."
        )

    active_count = count_subscriptions(ct["id"], status="active")
    total_count = count_subscriptions(ct["id"])
    subs = list_subscriptions(ct["id"])

    subs_text = ""
    active_subs = [s for s in subs if s["status"] == "active"]
    if active_subs:
        lines = []
        for s in active_subs:
            exp = datetime.fromisoformat(s["expires_at"].replace("Z", "+00:00")) if isinstance(s["expires_at"], str) else s["expires_at"]
            now = datetime.now(timezone.utc)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            days = max(0, (exp - now).days)
            lines.append(f"• {s.get('package_name', '?')} — {days} дн. осталось")
        subs_text = "\n\n*Активные подписки:*\n" + "\n".join(lines)

    await update.message.reply_text(
        f"👋 *{ct['name']}*\n\n"
        f"💰 Баланс: {ct['balance']}₽\n"
        f"📦 Подписок: {active_count} активных / {total_count} всего"
        + subs_text,
        parse_mode="Markdown",
    )


def register(app):
    app.add_handler(CommandHandler("start", cmd_start))
