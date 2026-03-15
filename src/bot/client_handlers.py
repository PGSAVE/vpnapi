from __future__ import annotations

from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import API_BASE_URL, DOCS_PASS, DOCS_URL, PANEL_SUB_URL
from src.models.client_token import get_client_token_by_telegram
from src.models.package import get_package
from src.models.subscription import (
    count_subscriptions,
    get_client_stats,
    get_subscription_for_client,
    list_subscriptions_page,
    search_subscriptions,
)
from src.services.subscription_service import APIError, delete_sub, renew_sub

# States
(
    CLIENT_MAIN,
    CLIENT_SUBS,
    CLIENT_SUB_DETAIL,
    CLIENT_SEARCH,
    CLIENT_CONFIRM,
) = range(5)

PAGE_SIZE = 8

def _esc(text: str) -> str:
    """Escape for Telegram Markdown v1 — only escape *, `, [."""
    return str(text).replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _get_ct(update: Update):
    tg_id = str(update.effective_user.id) if update.effective_user else "0"
    return get_client_token_by_telegram(tg_id)


def _ud(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    return ctx.user_data if ctx.user_data is not None else {}


def _days_left(expires_at_str) -> int:
    if isinstance(expires_at_str, str):
        exp = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    else:
        exp = expires_at_str
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return max(0, (exp - datetime.now(timezone.utc)).days)


def _status_emoji(status: str, expires_at) -> str:
    if status == "deleted":
        return "🗑"
    if status == "expired":
        return "⏰"
    if status == "active" and _days_left(expires_at) == 0:
        return "⚠️"
    return "✅"


async def _safe_edit(q, text, markup=None):
    try:
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


def _kb_main() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📦 Подписки", callback_data="cl_subs"),
            InlineKeyboardButton("📊 Статистика", callback_data="cl_stats"),
        ],
        [InlineKeyboardButton("🔍 Поиск подписки", callback_data="cl_search")],
        [InlineKeyboardButton("💳 Пополнить", url="https://t.me/saveroot")],
    ]
    if DOCS_URL:
        rows.append([InlineKeyboardButton("📄 Документация", callback_data="cl_docs")])
    return InlineKeyboardMarkup(rows)


def _kb_subs(
    subs: list, page: int, total: int, status_filter: str | None
) -> InlineKeyboardMarkup:
    rows = []
    for s in subs:
        emoji = _status_emoji(s["status"], s["expires_at"])
        days = _days_left(s["expires_at"])
        label = f"{emoji} {s.get('package_name', '?')} | {days}д | ID:{s['id']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cl_sub:{s['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cl_page:{page - 1}"))
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cl_page:{page + 1}"))
    rows.append(nav)

    filters_row = []
    for f_val, f_label in [
        ("active", "Активные"),
        ("expired", "Истёкшие"),
        (None, "Все"),
    ]:
        prefix = "● " if status_filter == f_val else ""
        cb = f"cl_filter:{f_val}" if f_val else "cl_filter:all"
        filters_row.append(InlineKeyboardButton(f"{prefix}{f_label}", callback_data=cb))
    rows.append(filters_row)

    rows.append([InlineKeyboardButton("🔙 Меню", callback_data="cl_back_main")])
    return InlineKeyboardMarkup(rows)


def _kb_sub_detail(sub: dict) -> InlineKeyboardMarkup:
    rows = []
    if sub.get("panel_subscription_token"):
        link = f"{PANEL_SUB_URL}/api/files/{sub['panel_subscription_token']}"
        rows.append([InlineKeyboardButton("🔗 Ссылка подписки", url=link)])
    if sub["status"] != "deleted":
        action_row = []
        action_row.append(
            InlineKeyboardButton("🔄 Продлить", callback_data=f"cl_renew:{sub['id']}")
        )
        action_row.append(
            InlineKeyboardButton("🗑 Удалить", callback_data=f"cl_delete:{sub['id']}")
        )
        rows.append(action_row)
    rows.append([InlineKeyboardButton("🔙 К подпискам", callback_data="cl_back_subs")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _fmt_sub_detail(s: dict) -> str:
    days = _days_left(s["expires_at"])
    emoji = _status_emoji(s["status"], s["expires_at"])
    status_text = {"active": "Активна", "expired": "Истекла", "deleted": "Удалена"}.get(
        s["status"], s["status"]
    )

    traffic = (
        "Безлимит" if not s.get("traffic_limit_gb") else f"{s['traffic_limit_gb']} GB"
    )
    lines = [
        f"{emoji} *Подписка #{s['id']}*",
        f"Пакет: *{_esc(s.get('package_name', '?'))}*",
        f"Статус: {status_text}",
        f"Трафик: `{traffic}`",
    ]
    if s["status"] == "active":
        lines.append(f"Осталось: `{days} дн.`")
    lines.append(f"Истекает: `{s['expires_at'][:10]}`")
    lines.append(f"Создана: `{s['created_at'][:10]}`")
    lines.append(f"Panel ID: `{_esc(s.get('panel_user_id', '—'))}`")
    return "\n".join(lines)


def _main_text(ct: dict) -> str:
    stats = get_client_stats(ct["id"])
    return (
        f"👋 *{_esc(ct['name'])}*\n\n"
        f"💰 Баланс: `{ct['balance']} ₽`\n"
        f"📦 Подписок: `{stats['active']}` активных / `{stats['total']}` всего\n\n"
        "Выберите действие:"
    )


def _enrich_sub(sub: dict) -> dict:
    pkg = get_package(sub["package_id"])
    if pkg:
        sub["package_name"] = pkg["name"]
        sub["traffic_limit_gb"] = pkg["traffic_limit_gb"]
    return sub


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return CLIENT_MAIN
    ct = _get_ct(update)
    if not ct:
        await update.message.reply_text(
            "👋 Привет!\n\n"
            "Ваш Telegram не привязан ни к одному токену.\n"
            "Обратитесь к администратору для получения доступа."
        )
        return ConversationHandler.END

    _ud(ctx)["ct_id"] = ct["id"]
    await update.message.reply_text(
        _main_text(ct), parse_mode="Markdown", reply_markup=_kb_main()
    )
    return CLIENT_MAIN


async def on_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CLIENT_MAIN
    await q.answer()
    data = q.data or ""
    ct = _get_ct(update)
    if not ct:
        return ConversationHandler.END
    ud = _ud(ctx)
    ud["ct_id"] = ct["id"]

    if data in ("cl_subs", "cl_back_subs_from_confirm"):
        ud["page"] = 0
        ud["filter"] = None
        return await _render_subs(q, ud, ct)

    if data == "cl_stats":
        stats = get_client_stats(ct["id"])
        text = (
            f"📊 *Статистика — {_esc(ct['name'])}*\n\n"
            f"💰 Баланс: `{ct['balance']} ₽`\n\n"
            f"*Подписки*\n"
            f"  Всего: `{stats['total']}`\n"
            f"  Активных: `{stats['active']}`\n"
            f"  Истёкших: `{stats['expired']}`\n"
            f"  Удалённых: `{stats['deleted']}`\n"
            f"  Просроченных: `{stats['overdue']}`\n\n"
            f"*Финансы*\n"
            f"  Пополнений: `{stats['topups']} ₽`\n"
            f"  Потрачено: `{stats['spent']} ₽`\n"
            f"  Покупок: `{stats['purchases']}`"
        )
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Меню", callback_data="cl_back_main")]]
        )
        await _safe_edit(q, text, markup)
        return CLIENT_MAIN

    if data == "cl_docs":
        docs_link = f"{API_BASE_URL}/{DOCS_URL}"
        text = f"📄 *Документация API*\n\nСсылка: {docs_link}\nПароль: `{DOCS_PASS}`"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Меню", callback_data="cl_back_main")]]
        )
        await _safe_edit(q, text, markup)
        return CLIENT_MAIN

    if data == "cl_search":
        await q.edit_message_text(
            "🔍 *Поиск подписки*\n\n"
            "Введите ID подписки, panel user ID или название пакета:",
            parse_mode="Markdown",
        )
        return CLIENT_SEARCH

    if data == "cl_back_main":
        await _safe_edit(q, _main_text(ct), _kb_main())
        return CLIENT_MAIN

    return CLIENT_MAIN


async def _render_subs(q, ud, ct) -> int:
    page = ud.get("page", 0)
    status_filter = ud.get("filter")
    total = count_subscriptions(ct["id"], status=status_filter)
    subs = list_subscriptions_page(
        ct["id"], offset=page * PAGE_SIZE, limit=PAGE_SIZE, status_filter=status_filter
    )
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    text = (
        f"📦 *Подписки* (стр. {page + 1}/{total_pages})"
        if subs
        else "📦 *Подписки*\n\n_Нет подписок_"
    )
    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=_kb_subs(subs, page, total, status_filter),
    )
    return CLIENT_SUBS


async def on_subs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CLIENT_SUBS
    await q.answer()
    data = q.data or ""
    ct = _get_ct(update)
    if not ct:
        return ConversationHandler.END
    ud = _ud(ctx)
    ud["ct_id"] = ct["id"]

    if data == "cl_back_main":
        await _safe_edit(q, _main_text(ct), _kb_main())
        return CLIENT_MAIN

    if data.startswith("cl_page:"):
        ud["page"] = int(data.split(":")[1])
        return await _render_subs(q, ud, ct)

    if data.startswith("cl_filter:"):
        val = data.split(":")[1]
        ud["filter"] = None if val == "all" else val
        ud["page"] = 0
        return await _render_subs(q, ud, ct)

    if data.startswith("cl_sub:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription_for_client(sub_id, ct["id"])
        if not sub:
            await q.answer("Подписка не найдена", show_alert=True)
            return CLIENT_SUBS
        _enrich_sub(sub)
        await q.edit_message_text(
            _fmt_sub_detail(sub),
            parse_mode="Markdown",
            reply_markup=_kb_sub_detail(sub),
        )
        return CLIENT_SUB_DETAIL

    return CLIENT_SUBS


async def on_sub_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CLIENT_SUB_DETAIL
    await q.answer()
    data = q.data or ""
    ct = _get_ct(update)
    if not ct:
        return ConversationHandler.END
    ud = _ud(ctx)

    if data == "cl_back_subs":
        return await _render_subs(q, ud, ct)

    if data == "cl_back_main":
        await _safe_edit(q, _main_text(ct), _kb_main())
        return CLIENT_MAIN

    if data.startswith("cl_renew:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription_for_client(sub_id, ct["id"])
        if not sub:
            await q.answer("Подписка не найдена", show_alert=True)
            return CLIENT_SUB_DETAIL
        _enrich_sub(sub)
        pkg = get_package(sub["package_id"])
        price = pkg["price"] if pkg else 0
        duration = pkg["duration_days"] if pkg else 0
        ud["confirm_action"] = "renew"
        ud["confirm_sub_id"] = sub_id
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Да, продлить", callback_data="cl_confirm_yes"
                    ),
                    InlineKeyboardButton("❌ Отмена", callback_data="cl_confirm_no"),
                ]
            ]
        )
        await q.edit_message_text(
            f"🔄 *Продление подписки #{sub_id}*\n\n"
            f"Пакет: *{_esc(sub.get('package_name', '?'))}*\n"
            f"Стоимость: `{price} ₽`\n"
            f"Срок: `+{duration} дн.`\n"
            f"Ваш баланс: `{ct['balance']} ₽`\n\n"
            "Подтвердите продление:",
            parse_mode="Markdown",
            reply_markup=markup,
        )
        return CLIENT_CONFIRM

    if data.startswith("cl_delete:"):
        sub_id = int(data.split(":")[1])
        ud["confirm_action"] = "delete"
        ud["confirm_sub_id"] = sub_id
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Да, удалить", callback_data="cl_confirm_yes"
                    ),
                    InlineKeyboardButton("❌ Отмена", callback_data="cl_confirm_no"),
                ]
            ]
        )
        await q.edit_message_text(
            f"🗑 *Удаление подписки #{sub_id}*\n\n"
            "Подписка будет удалена без возможности восстановления.\n"
            "Подтвердите удаление:",
            parse_mode="Markdown",
            reply_markup=markup,
        )
        return CLIENT_CONFIRM

    return CLIENT_SUB_DETAIL


async def on_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return CLIENT_CONFIRM
    await q.answer()
    data = q.data or ""
    ct = _get_ct(update)
    if not ct:
        return ConversationHandler.END
    ud = _ud(ctx)

    action = ud.pop("confirm_action", None)
    sub_id = ud.pop("confirm_sub_id", None)

    if data == "cl_confirm_no" or not action or not sub_id:
        # Go back to sub detail if possible, else main
        if sub_id:
            sub = get_subscription_for_client(sub_id, ct["id"])
            if sub:
                _enrich_sub(sub)
                await q.edit_message_text(
                    _fmt_sub_detail(sub),
                    parse_mode="Markdown",
                    reply_markup=_kb_sub_detail(sub),
                )
                return CLIENT_SUB_DETAIL
        await _safe_edit(q, _main_text(ct), _kb_main())
        return CLIENT_MAIN

    if data == "cl_confirm_yes":
        if action == "renew":
            try:
                sub = renew_sub(ct, sub_id)
                _enrich_sub(sub)
                await q.edit_message_text(
                    f"✅ *Подписка #{sub_id} продлена!*\n\n" + _fmt_sub_detail(sub),
                    parse_mode="Markdown",
                    reply_markup=_kb_sub_detail(sub),
                )
            except APIError as e:
                await q.edit_message_text(
                    f"❌ Ошибка: {_esc(str(e))}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 Меню", callback_data="cl_back_main"
                                )
                            ]
                        ]
                    ),
                )
                return CLIENT_MAIN
            return CLIENT_SUB_DETAIL

        if action == "delete":
            try:
                sub = delete_sub(sub_id, ct["id"])
                await q.edit_message_text(
                    f"🗑 *Подписка #{sub_id} удалена.*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 К подпискам",
                                    callback_data="cl_back_subs_from_confirm",
                                )
                            ]
                        ]
                    ),
                )
            except APIError as e:
                await q.edit_message_text(
                    f"❌ Ошибка: {_esc(str(e))}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 Меню", callback_data="cl_back_main"
                                )
                            ]
                        ]
                    ),
                )
            return CLIENT_MAIN

    return CLIENT_MAIN


async def on_search_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return CLIENT_SEARCH
    ct = _get_ct(update)
    if not ct:
        return ConversationHandler.END
    ud = _ud(ctx)
    ud["ct_id"] = ct["id"]

    query = (update.message.text or "").strip()
    if not query:
        await update.message.reply_text("❌ Введите поисковый запрос.")
        return CLIENT_SEARCH

    # Direct ID lookup
    if query.isdigit():
        sub = get_subscription_for_client(int(query), ct["id"])
        if sub:
            _enrich_sub(sub)
            await update.message.reply_text(
                _fmt_sub_detail(sub),
                parse_mode="Markdown",
                reply_markup=_kb_sub_detail(sub),
            )
            return CLIENT_SUB_DETAIL

    results = search_subscriptions(ct["id"], query)

    if not results:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Меню", callback_data="cl_back_main")]]
        )
        await update.message.reply_text(
            f"🔍 По запросу `{_esc(query)}` ничего не найдено.",
            parse_mode="Markdown",
            reply_markup=markup,
        )
        return CLIENT_MAIN

    rows = []
    for s in results:
        emoji = _status_emoji(s["status"], s["expires_at"])
        days = _days_left(s["expires_at"])
        label = f"{emoji} {s.get('package_name', '?')} | {days}д | ID:{s['id']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cl_sub:{s['id']}")])
    rows.append([InlineKeyboardButton("🔙 Меню", callback_data="cl_back_main")])

    await update.message.reply_text(
        f"🔍 Найдено: `{len(results)}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return CLIENT_SUBS


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app) -> None:
    _txt = filters.TEXT & ~filters.COMMAND

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CLIENT_MAIN: [
                CallbackQueryHandler(
                    on_main,
                    pattern="^(cl_subs|cl_stats|cl_search|cl_docs|cl_back_main|cl_back_subs_from_confirm)$",
                ),
            ],
            CLIENT_SUBS: [
                CallbackQueryHandler(
                    on_subs,
                    pattern="^(cl_back_main|cl_page:\\d+|cl_filter:\\w+|cl_sub:\\d+|noop)$",
                ),
            ],
            CLIENT_SUB_DETAIL: [
                CallbackQueryHandler(
                    on_sub_detail,
                    pattern="^(cl_back_subs|cl_back_main|cl_renew:\\d+|cl_delete:\\d+)$",
                ),
            ],
            CLIENT_CONFIRM: [
                CallbackQueryHandler(
                    on_confirm,
                    pattern="^(cl_confirm_yes|cl_confirm_no)$",
                ),
            ],
            CLIENT_SEARCH: [
                MessageHandler(_txt, on_search_input),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
