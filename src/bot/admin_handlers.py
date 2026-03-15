from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from src.config import ADMIN_TELEGRAM_ID
from src.models.package import create_package, list_packages, update_package, get_package
from src.models.client_token import create_client_token, list_client_tokens, get_client_token_by_id, update_balance
from src.models.subscription import count_subscriptions
from src.models.transaction import create_transaction, get_stats


def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == str(ADMIN_TELEGRAM_ID)


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "⚙️ *Админ-панель*\n\n"
        "/packages — Список пакетов\n"
        "/addpkg — Создать пакет\n"
        "/tokens — Список токенов\n"
        "/addtoken — Создать токен\n"
        "/topup — Пополнить баланс\n"
        "/stats — Статистика",
        parse_mode="Markdown",
    )


async def cmd_packages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    pkgs = list_packages(active_only=False)
    if not pkgs:
        return await update.message.reply_text("Пакетов нет. /addpkg")
    lines = []
    for p in pkgs:
        status = "✅" if p["active"] else "❌"
        lines.append(
            f"{status} *{p['name']}*\n"
            f"  {p['traffic_limit_gb']}GB | {p['max_devices']} устр. | {p['duration_days']}д | {p['price']}₽\n"
            f"  ID: `{p['id']}`"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_addpkg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    args = " ".join(ctx.args) if ctx.args else ""
    parts = [s.strip() for s in args.split("|")]
    if len(parts) < 5:
        return await update.message.reply_text(
            "Формат: /addpkg name|trafficGB|devices|days|price|description\n"
            "Пример: /addpkg Basic|50|2|30|100|Базовый пакет"
        )
    name, traffic, devices, days, price = parts[:5]
    desc = "|".join(parts[5:]) if len(parts) > 5 else ""
    pkg = create_package(name, float(traffic), int(devices), int(days), float(price), desc)
    await update.message.reply_text(f"✅ Пакет создан: *{pkg['name']}*\nID: `{pkg['id']}`", parse_mode="Markdown")


async def cmd_delpkg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        return await update.message.reply_text("Формат: /delpkg <id>")
    pkg = get_package(int(ctx.args[0]))
    if not pkg:
        return await update.message.reply_text("Пакет не найден")
    new_active = 0 if pkg["active"] else 1
    update_package(pkg["id"], active=new_active)
    status = "✅ активен" if new_active else "❌ отключён"
    await update.message.reply_text(f"Пакет *{pkg['name']}*: {status}", parse_mode="Markdown")


async def cmd_editpkg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Формат: /editpkg <id> field=value ...")
    pkg = get_package(int(ctx.args[0]))
    if not pkg:
        return await update.message.reply_text("Пакет не найден")
    updates = {}
    num_fields = {"traffic_limit_gb", "max_devices", "duration_days", "price"}
    for pair in ctx.args[1:]:
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if k in num_fields:
            updates[k] = float(v)
        elif k in ("name", "description"):
            updates[k] = v
    update_package(pkg["id"], **updates)
    await update.message.reply_text(f"✅ Пакет *{pkg['name']}* обновлён", parse_mode="Markdown")


async def cmd_tokens(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    tokens = list_client_tokens()
    if not tokens:
        return await update.message.reply_text("Токенов нет. /addtoken")
    lines = []
    for t in tokens:
        status = "✅" if t["active"] else "❌"
        lines.append(
            f"{status} *{t['name']}*\n"
            f"  Баланс: {t['balance']}₽ | TG: {t['telegram_user_id'] or '—'}\n"
            f"  Токен: `{t['token'][:8]}...`\n"
            f"  ID: `{t['id']}`"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_addtoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        return await update.message.reply_text("Формат: /addtoken name [tg_user_id]")
    name = ctx.args[0]
    tg_id = ctx.args[1] if len(ctx.args) > 1 else None
    ct = create_client_token(name, tg_id)
    await update.message.reply_text(
        f"✅ Токен создан\nИмя: *{ct['name']}*\nТокен: `{ct['token']}`\nTG: {ct['telegram_user_id'] or '—'}",
        parse_mode="Markdown",
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Формат: /topup <token_id> <amount>")
    ct = get_client_token_by_id(int(ctx.args[0]))
    if not ct:
        return await update.message.reply_text("Токен не найден")
    amount = float(ctx.args[1])
    if amount <= 0:
        return await update.message.reply_text("Некорректная сумма")
    new_balance = update_balance(ct["id"], amount)
    create_transaction(ct["id"], amount, "topup", "Admin top-up")
    await update.message.reply_text(f"✅ Баланс *{ct['name']}*: {new_balance}₽ (+{amount})", parse_mode="Markdown")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    total_subs = count_subscriptions()
    active_subs = count_subscriptions(status="active")
    total_tokens = len(list_client_tokens())
    tx_stats = get_stats()
    topups = tx_stats.get("topup", 0)
    charges = abs(tx_stats.get("charge", 0))
    await update.message.reply_text(
        "📊 *Статистика*\n\n"
        f"Подписок всего: {total_subs}\n"
        f"Активных: {active_subs}\n"
        f"Токенов: {total_tokens}\n"
        f"Пополнения: {topups}₽\n"
        f"Списания: {charges}₽",
        parse_mode="Markdown",
    )


def register(app):
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("packages", cmd_packages))
    app.add_handler(CommandHandler("addpkg", cmd_addpkg))
    app.add_handler(CommandHandler("delpkg", cmd_delpkg))
    app.add_handler(CommandHandler("editpkg", cmd_editpkg))
    app.add_handler(CommandHandler("tokens", cmd_tokens))
    app.add_handler(CommandHandler("addtoken", cmd_addtoken))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("stats", cmd_stats))
