from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.models.client_token import get_client_token_by_telegram
from src.models.subscription import count_subscriptions, list_subscriptions


def _kb_refresh() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Обновить", callback_data="client_refresh")]]
    )


async def _build_status_text(tg_id: str) -> str:
    ct = get_client_token_by_telegram(tg_id)

    if not ct:
        return (
            "👋 Привет!\n\n"
            "Ваш Telegram не привязан ни к одному токену.\n"
            "Обратитесь к администратору для получения доступа."
        )

    active_count = count_subscriptions(ct["id"], status="active")
    total_count = count_subscriptions(ct["id"])
    subs = list_subscriptions(ct["id"])

    lines: list[str] = []
    active_subs = [s for s in subs if s["status"] == "active"]
    if active_subs:
        for s in active_subs:
            raw_exp = s["expires_at"]
            if isinstance(raw_exp, str):
                exp = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
            else:
                exp = raw_exp
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            days = max(0, (exp - datetime.now(timezone.utc)).days)
            lines.append(f"• {s.get('package_name', '?')} — {days} дн. осталось")

    subs_block = ("\n\n*Активные подписки:*\n" + "\n".join(lines)) if lines else ""

    return (
        f"👋 *{ct['name']}*\n\n"
        f"💰 Баланс: `{ct['balance']} ₽`\n"
        f"📦 Подписок: `{active_count}` активных / `{total_count}` всего" + subs_block
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return
    tg_id = str(update.effective_user.id) if update.effective_user else "0"
    text = await _build_status_text(tg_id)
    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=_kb_refresh(),
    )


async def on_client_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None:
        return
    await q.answer("Обновлено ✅")
    tg_id = str(update.effective_user.id) if update.effective_user else "0"
    text = await _build_status_text(tg_id)
    try:
        await q.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=_kb_refresh(),
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


def register(app) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_client_refresh, pattern="^client_refresh$"))
