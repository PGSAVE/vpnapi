from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import ADMIN_TELEGRAM_ID
from src.models.client_token import (
    create_client_token,
    get_client_token_by_id,
    list_client_tokens,
    update_balance,
)
from src.models.package import (
    create_package,
    get_package,
    list_packages,
    update_package,
)
from src.models.subscription import count_expired, count_new_subscriptions_today, count_subscriptions
from src.models.transaction import create_transaction, get_detailed_stats
from src.services.panel_api import list_groups as fetch_groups

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------
(
    MAIN,
    PACKAGES,
    PKG_CREATE_NAME,
    PKG_CREATE_TRAFFIC,
    PKG_CREATE_DEVICES,
    PKG_CREATE_DAYS,
    PKG_CREATE_PRICE,
    PKG_CREATE_DESC,
    PKG_CREATE_GROUPS,
    PKG_DETAIL,
    PKG_EDIT_MENU,
    PKG_EDIT_VALUE,
    PKG_EDIT_GROUPS,
    TOKENS,
    TOKEN_CREATE_NAME,
    TOKEN_CREATE_TG,
    TOKEN_DETAIL,
    TOKEN_TOPUP_AMOUNT,
    STATS,
) = range(19)

# ---------------------------------------------------------------------------
# Auth / helpers
# ---------------------------------------------------------------------------


def is_admin(update: Update) -> bool:
    if update.effective_user is None:
        return False
    return str(update.effective_user.id) == str(ADMIN_TELEGRAM_ID)


def _ud(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    """Return user_data dict, never None."""
    return ctx.user_data if ctx.user_data is not None else {}


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 Пакеты", callback_data="packages"),
                InlineKeyboardButton("👥 Токены", callback_data="tokens"),
            ],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        ]
    )


def kb_packages(pkgs: list) -> InlineKeyboardMarkup:
    rows = []
    for p in pkgs:
        mark = "✅" if p["active"] else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark} {p['name']}  {p['price']}₽ / {p['duration_days']}д",
                    callback_data=f"pkg_detail:{p['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("➕ Создать пакет", callback_data="pkg_create")])
    rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_pkg_detail(pkg: dict) -> InlineKeyboardMarkup:
    toggle = "❌ Деактивировать" if pkg["active"] else "✅ Активировать"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✏️ Редактировать", callback_data=f"pkg_edit:{pkg['id']}"
                )
            ],
            [InlineKeyboardButton(toggle, callback_data=f"pkg_toggle:{pkg['id']}")],
            [InlineKeyboardButton("🔙 К пакетам", callback_data="back_packages")],
        ]
    )


def kb_pkg_edit_menu(pkg_id: int) -> InlineKeyboardMarkup:
    pid = str(pkg_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📝 Название", callback_data=f"pef:{pid}:name"),
                InlineKeyboardButton(
                    "📄 Описание", callback_data=f"pef:{pid}:description"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📶 Трафик (GB)", callback_data=f"pef:{pid}:traffic_limit_gb"
                ),
                InlineKeyboardButton(
                    "📱 Устройства", callback_data=f"pef:{pid}:max_devices"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📅 Дней", callback_data=f"pef:{pid}:duration_days"
                ),
                InlineKeyboardButton("💰 Цена (₽)", callback_data=f"pef:{pid}:price"),
            ],
            [InlineKeyboardButton("🌐 Группы", callback_data=f"pkg_edit_groups:{pid}")],
            [
                InlineKeyboardButton(
                    "🔙 К пакету", callback_data=f"back_pkg_detail:{pid}"
                )
            ],
        ]
    )


def kb_groups(all_groups: list[dict], selected: set) -> InlineKeyboardMarkup:
    """Groups referenced by index so callback_data stays within 64 bytes.

    ``all_groups`` is a list of ``{"_id": ..., "name": ...}`` dicts.
    ``selected`` is a set of ``_id`` strings.
    """
    rows = []
    for i, g in enumerate(all_groups):
        mark = "✅" if g["_id"] in selected else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {g['name']}", callback_data=f"grp:{i}")])
    rows.append([InlineKeyboardButton("✓ Готово", callback_data="grp_done")])
    return InlineKeyboardMarkup(rows)


def kb_tokens(tokens: list) -> InlineKeyboardMarkup:
    rows = []
    for t in tokens:
        mark = "✅" if t["active"] else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark} {t['name']}  {t['balance']}₽",
                    callback_data=f"token_detail:{t['id']}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("➕ Создать токен", callback_data="token_create")]
    )
    rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_token_detail(token: dict) -> InlineKeyboardMarkup:
    toggle = "❌ Деактивировать" if token["active"] else "✅ Активировать"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Пополнить баланс", callback_data=f"token_topup:{token['id']}"
                )
            ],
            [InlineKeyboardButton(toggle, callback_data=f"token_toggle:{token['id']}")],
            [InlineKeyboardButton("🔙 К токенам", callback_data="back_tokens")],
        ]
    )


def kb_skip(cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Пропустить ➡️", callback_data=cb)]]
    )


# ---------------------------------------------------------------------------
# Text formatters
# ---------------------------------------------------------------------------

_MD = str.maketrans({"_": r"\_", "*": r"\*", "`": r"\`", "[": r"\["})


def _esc(text: str) -> str:
    return str(text).translate(_MD)


def fmt_pkg(p: dict) -> str:
    status = "✅ Активен" if p["active"] else "❌ Отключён"
    raw_groups = p.get("groups") or []
    groups = ", ".join(
        g["name"] if isinstance(g, dict) else str(g) for g in raw_groups
    ) or "—"
    desc = _esc(p["description"]) if p.get("description") else "—"
    return (
        f"📦 *{_esc(p['name'])}*\n"
        f"Статус: {status}\n"
        f"Трафик: `{'Безлимит' if not p['traffic_limit_gb'] else str(p['traffic_limit_gb']) + ' GB'}`\n"
        f"Устройств: `{p['max_devices']}`\n"
        f"Длительность: `{p['duration_days']} дней`\n"
        f"Цена: `{p['price']} ₽`\n"
        f"Группы: {_esc(groups)}\n"
        f"Описание: {desc}\n"
        f"ID: `{p['id']}`"
    )


def fmt_token(t: dict) -> str:
    status = "✅ Активен" if t["active"] else "❌ Отключён"
    tg = t["telegram_user_id"] or "—"
    return (
        f"👤 *{_esc(t['name'])}*\n"
        f"Статус: {status}\n"
        f"Баланс: `{t['balance']} ₽`\n"
        f"Telegram ID: `{tg}`\n"
        f"Токен: `{t['token']}`\n"
        f"ID: `{t['id']}`"
    )


# ---------------------------------------------------------------------------
# Shared render helpers
# ---------------------------------------------------------------------------


async def _edit_or_reply(
    update: Update,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=markup
        )


async def _show_main(update: Update) -> None:
    await _edit_or_reply(update, "⚙️ *Админ-панель*\nВыберите раздел:", kb_main())


async def _show_packages(update: Update) -> None:
    pkgs = list_packages(active_only=False)
    await _edit_or_reply(update, "📦 *Пакеты*", kb_packages(pkgs))


async def _show_tokens(update: Update) -> None:
    tokens = list_client_tokens()
    await _edit_or_reply(update, "👥 *Токены*", kb_tokens(tokens))


async def _show_pkg_detail(update: Update, pkg_id: int) -> None:
    pkg = get_package(pkg_id)
    if not pkg:
        if update.callback_query:
            await update.callback_query.answer("Пакет не найден", show_alert=True)
        return
    await _edit_or_reply(update, fmt_pkg(pkg), kb_pkg_detail(pkg))


async def _show_token_detail(update: Update, token_id: int) -> None:
    token = get_client_token_by_id(token_id)
    if not token:
        if update.callback_query:
            await update.callback_query.answer("Токен не найден", show_alert=True)
        return
    await _edit_or_reply(update, fmt_token(token), kb_token_detail(token))


async def _show_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ud = _ud(ctx)
    all_groups = ud.get("all_groups", [])
    selected = ud.get("selected_groups", set())
    if all_groups:
        header = (
            "🌐 *Выберите группы* для пакета:\n"
            "Нажмите на группу, чтобы добавить или убрать её."
        )
    else:
        header = "🌐 *Группы*\n_(Нет доступных групп из панели — нажмите «✓ Готово»)_"
    await _edit_or_reply(update, header, kb_groups(all_groups, selected))


async def _show_stats(update: Update) -> None:
    total_subs = count_subscriptions()
    active_subs = count_subscriptions(status="active")
    deleted_subs = count_subscriptions(status="deleted")
    expired_subs = count_expired()
    new_today = count_new_subscriptions_today()
    tokens = list_client_tokens()
    total_tokens = len(tokens)
    active_tokens = sum(1 for t in tokens if t["active"])
    total_balance = sum(t["balance"] for t in tokens)

    tx = get_detailed_stats()
    by_type = tx.get("by_type", {})
    topups_all = by_type.get("topup", {}).get("total", 0)
    charges_all = abs(by_type.get("charge", {}).get("total", 0))
    refunds_all = by_type.get("refund", {}).get("total", 0) or 0

    today = tx.get("today", {})
    week = tx.get("week", {})
    month = tx.get("month", {})

    top_pkgs = tx.get("top_packages", [])
    top_lines = "\n".join(
        f"  `{i+1}.` {_esc(p['name'])} — {p['count']} шт, `{p['revenue']} ₽`"
        for i, p in enumerate(top_pkgs)
    ) if top_pkgs else "  _нет данных_"

    text = (
        "📊 *Статистика*\n\n"
        "*Подписки*\n"
        f"  Всего: `{total_subs}` | Активных: `{active_subs}`\n"
        f"  Просрочено: `{expired_subs}` | Удалено: `{deleted_subs}`\n"
        f"  Новых сегодня: `{new_today}`\n\n"
        "*Клиенты*\n"
        f"  Токенов: `{total_tokens}` (активных: `{active_tokens}`)\n"
        f"  Общий баланс: `{total_balance} ₽`\n\n"
        "*Финансы — сегодня*\n"
        f"  Пополнения: `{today['topups']} ₽` | Продажи: `{today['charges']} ₽` ({today['sales']} шт)\n\n"
        "*Финансы — 7 дней*\n"
        f"  Пополнения: `{week['topups']} ₽` | Продажи: `{week['charges']} ₽` ({week['sales']} шт)\n\n"
        "*Финансы — 30 дней*\n"
        f"  Пополнения: `{month['topups']} ₽` | Продажи: `{month['charges']} ₽` ({month['sales']} шт)\n\n"
        "*Финансы — всё время*\n"
        f"  Пополнения: `{topups_all} ₽`\n"
        f"  Списания: `{charges_all} ₽`\n"
        f"  Возвраты: `{refunds_all} ₽`\n\n"
        f"*Топ пакетов по продажам*\n{top_lines}"
    )
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Обновить", callback_data="stats_refresh")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="back_main")],
        ]
    )
    await _edit_or_reply(update, text, markup)


def _toggle_group(cb_data: str, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a group by its list index stored in callback_data 'grp:{index}'."""
    ud = _ud(ctx)
    idx = int(cb_data.split(":")[1])
    all_groups = ud.get("all_groups", [])
    if idx >= len(all_groups):
        return
    gid: str = all_groups[idx]["_id"]
    selected: set = ud.setdefault("selected_groups", set())
    if gid in selected:
        selected.discard(gid)
    else:
        selected.add(gid)


# ---------------------------------------------------------------------------
# Entry / cancel
# ---------------------------------------------------------------------------


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        return ConversationHandler.END
    _ud(ctx).clear()
    await _show_main(update)
    return MAIN


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _ud(ctx).clear()
    if update.message:
        await update.message.reply_text(
            "⚙️ *Главное меню*", parse_mode="Markdown", reply_markup=kb_main()
        )
    return MAIN


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


async def on_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return MAIN
    await q.answer()
    data = q.data or ""
    if data == "packages":
        await _show_packages(update)
        return PACKAGES
    if data == "tokens":
        await _show_tokens(update)
        return TOKENS
    if data == "stats":
        await _show_stats(update)
        return STATS
    return MAIN


# ---------------------------------------------------------------------------
# PACKAGES LIST
# ---------------------------------------------------------------------------


async def on_packages(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return PACKAGES
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "back_main":
        await _show_main(update)
        return MAIN

    if data == "pkg_create":
        ud["pkg_new"] = {}
        await q.edit_message_text(
            "📦 *Создание пакета — шаг 1 из 7*\n\nВведите *название* пакета:",
            parse_mode="Markdown",
        )
        return PKG_CREATE_NAME

    if data.startswith("pkg_detail:"):
        pkg_id = int(data.split(":")[1])
        ud["pkg_id"] = pkg_id
        await _show_pkg_detail(update, pkg_id)
        return PKG_DETAIL

    await _show_packages(update)
    return PACKAGES


# ---------------------------------------------------------------------------
# PKG CREATE — sequential steps
# ---------------------------------------------------------------------------


async def on_pkg_create_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_CREATE_NAME
    _ud(ctx).setdefault("pkg_new", {})["name"] = (update.message.text or "").strip()
    await update.message.reply_text(
        "📦 *Шаг 2 из 7*\n\nВведите *объём трафика* в GB (например: `50`)\n"
        "или `0` / `безлимит` для безлимитного трафика:",
        parse_mode="Markdown",
    )
    return PKG_CREATE_TRAFFIC


async def on_pkg_create_traffic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_CREATE_TRAFFIC
    raw = (update.message.text or "").strip().lower()
    if raw in ("0", "безлимит", "unlim", "unlimited"):
        val = 0.0
    else:
        try:
            val = float(raw.replace(",", "."))
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Введите положительное число, `0` или `безлимит`",
                parse_mode="Markdown",
            )
            return PKG_CREATE_TRAFFIC
    _ud(ctx).setdefault("pkg_new", {})["traffic_limit_gb"] = val
    await update.message.reply_text(
        "📦 *Шаг 3 из 7*\n\nВведите *максимальное кол-во устройств* (например: `2`):",
        parse_mode="Markdown",
    )
    return PKG_CREATE_DEVICES


async def on_pkg_create_devices(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_CREATE_DEVICES
    try:
        val = int((update.message.text or "").strip())
        if val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите целое положительное число, например: `2`", parse_mode="Markdown"
        )
        return PKG_CREATE_DEVICES
    _ud(ctx).setdefault("pkg_new", {})["max_devices"] = val
    await update.message.reply_text(
        "📦 *Шаг 4 из 7*\n\nВведите *длительность* в днях (например: `30`):",
        parse_mode="Markdown",
    )
    return PKG_CREATE_DAYS


async def on_pkg_create_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_CREATE_DAYS
    try:
        val = int((update.message.text or "").strip())
        if val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите целое положительное число, например: `30`",
            parse_mode="Markdown",
        )
        return PKG_CREATE_DAYS
    _ud(ctx).setdefault("pkg_new", {})["duration_days"] = val
    await update.message.reply_text(
        "📦 *Шаг 5 из 7*\n\nВведите *цену* в рублях (например: `299`):",
        parse_mode="Markdown",
    )
    return PKG_CREATE_PRICE


async def on_pkg_create_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_CREATE_PRICE
    try:
        val = float((update.message.text or "").strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите число, например: `299`", parse_mode="Markdown"
        )
        return PKG_CREATE_PRICE
    _ud(ctx).setdefault("pkg_new", {})["price"] = val
    await update.message.reply_text(
        "📦 *Шаг 6 из 7*\n\nВведите *описание* пакета (необязательно):",
        parse_mode="Markdown",
        reply_markup=kb_skip("skip_desc"),
    )
    return PKG_CREATE_DESC


async def on_pkg_create_desc_text(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    if not update.message:
        return PKG_CREATE_DESC
    _ud(ctx).setdefault("pkg_new", {})["description"] = (
        update.message.text or ""
    ).strip()
    return await _enter_create_groups(update, ctx)


async def on_pkg_create_desc_skip(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    _ud(ctx).setdefault("pkg_new", {})["description"] = ""
    return await _enter_create_groups(update, ctx)


async def _enter_create_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ud = _ud(ctx)
    ud["all_groups"] = fetch_groups()
    ud["selected_groups"] = set()
    await _show_groups(update, ctx)
    return PKG_CREATE_GROUPS


async def on_pkg_create_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return PKG_CREATE_GROUPS
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "grp_done":
        selected = ud.get("selected_groups", set())
        all_groups = ud.get("all_groups", [])
        groups_to_save = [g for g in all_groups if g["_id"] in selected]
        d = ud.get("pkg_new", {})
        pkg = create_package(
            d.get("name", ""),
            d.get("traffic_limit_gb", 0),
            d.get("max_devices", 1),
            d.get("duration_days", 30),
            d.get("price", 0),
            d.get("description", ""),
            groups_to_save,
        )
        ud.pop("pkg_new", None)
        ud["pkg_id"] = pkg["id"]
        await q.edit_message_text(
            f"✅ *Пакет создан!*\n\n{fmt_pkg(pkg)}",
            parse_mode="Markdown",
            reply_markup=kb_pkg_detail(pkg),
        )
        return PKG_DETAIL

    if data.startswith("grp:"):
        _toggle_group(data, ctx)
        await q.edit_message_reply_markup(
            reply_markup=kb_groups(
                ud.get("all_groups", []),
                ud.get("selected_groups", set()),
            )
        )
        return PKG_CREATE_GROUPS

    return PKG_CREATE_GROUPS


# ---------------------------------------------------------------------------
# PKG DETAIL
# ---------------------------------------------------------------------------


async def on_pkg_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return PKG_DETAIL
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "back_packages":
        await _show_packages(update)
        return PACKAGES

    if data.startswith("pkg_edit:"):
        pkg_id = int(data.split(":")[1])
        ud["pkg_id"] = pkg_id
        pkg = get_package(pkg_id)
        if not pkg:
            await q.answer("Пакет не найден", show_alert=True)
            return PKG_DETAIL
        await q.edit_message_text(
            f"✏️ *Редактирование: {_esc(pkg['name'])}*\n\nВыберите поле для изменения:",
            parse_mode="Markdown",
            reply_markup=kb_pkg_edit_menu(pkg_id),
        )
        return PKG_EDIT_MENU

    if data.startswith("pkg_toggle:"):
        pkg_id = int(data.split(":")[1])
        pkg = get_package(pkg_id)
        if not pkg:
            await q.answer("Пакет не найден", show_alert=True)
            return PKG_DETAIL
        new_active = 0 if pkg["active"] else 1
        pkg = update_package(pkg_id, active=new_active)
        status_text = "✅ активирован" if new_active else "❌ деактивирован"
        await q.answer(f"Пакет {status_text}")
        if pkg:
            await q.edit_message_text(
                fmt_pkg(pkg), parse_mode="Markdown", reply_markup=kb_pkg_detail(pkg)
            )
        return PKG_DETAIL

    return PKG_DETAIL


# ---------------------------------------------------------------------------
# PKG EDIT MENU
# ---------------------------------------------------------------------------

_FIELD_LABELS = {
    "name": "Название",
    "description": "Описание",
    "traffic_limit_gb": "Объём трафика в GB",
    "max_devices": "Кол-во устройств",
    "duration_days": "Длительность (дней)",
    "price": "Цена (₽)",
}


async def on_pkg_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return PKG_EDIT_MENU
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data.startswith("back_pkg_detail:"):
        pkg_id = int(data.split(":")[1])
        ud["pkg_id"] = pkg_id
        await _show_pkg_detail(update, pkg_id)
        return PKG_DETAIL

    if data.startswith("pkg_edit_groups:"):
        pkg_id = int(data.split(":")[1])
        pkg = get_package(pkg_id)
        ud["pkg_id"] = pkg_id
        ud["all_groups"] = fetch_groups()
        ud["selected_groups"] = {g["_id"] for g in (pkg.get("groups") or []) if isinstance(g, dict)} if pkg else set()
        await _show_groups(update, ctx)
        return PKG_EDIT_GROUPS

    if data.startswith("pef:"):
        _, pkg_id_str, field = data.split(":", 2)
        pkg_id = int(pkg_id_str)
        pkg = get_package(pkg_id)
        if not pkg:
            await q.answer("Пакет не найден", show_alert=True)
            return PKG_EDIT_MENU
        ud["pkg_id"] = pkg_id
        ud["edit_field"] = field
        label = _FIELD_LABELS.get(field, field)
        current_val = pkg.get(field, "")
        if field in ("max_devices", "duration_days"):
            hint = " (целое число)"
        elif field == "traffic_limit_gb":
            hint = " (или `0` / `безлимит`)"
        else:
            hint = ""
        await q.edit_message_text(
            f"✏️ *{label}*\n\n"
            f"Текущее значение: `{_esc(str(current_val))}`\n\n"
            f"Введите новое значение{hint}:",
            parse_mode="Markdown",
        )
        return PKG_EDIT_VALUE

    return PKG_EDIT_MENU


# ---------------------------------------------------------------------------
# PKG EDIT VALUE (text input)
# ---------------------------------------------------------------------------


async def on_pkg_edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return PKG_EDIT_VALUE
    ud = _ud(ctx)
    pkg_id = ud.get("pkg_id")
    field = ud.get("edit_field")
    raw = (update.message.text or "").strip()

    if not pkg_id or not field:
        await update.message.reply_text("⚠️ Ошибка состояния. Используйте /admin")
        return ConversationHandler.END

    if field in ("max_devices", "duration_days"):
        try:
            value: int | float | str = int(raw)
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Введите целое положительное число.", parse_mode="Markdown"
            )
            return PKG_EDIT_VALUE
    elif field == "traffic_limit_gb":
        if raw.lower() in ("0", "безлимит", "unlim", "unlimited"):
            value = 0.0
        else:
            try:
                value = float(raw.replace(",", "."))
                if value < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(
                    "❌ Введите число, `0` или `безлимит`.", parse_mode="Markdown"
                )
                return PKG_EDIT_VALUE
    elif field == "price":
        try:
            value = float(raw.replace(",", "."))
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Введите корректное число.", parse_mode="Markdown"
            )
            return PKG_EDIT_VALUE
    else:
        value = raw

    update_package(pkg_id, **{field: value})
    ud.pop("edit_field", None)
    pkg = get_package(pkg_id)
    label = _FIELD_LABELS.get(field, field)
    name_str = _esc(pkg["name"]) if pkg else str(pkg_id)
    await update.message.reply_text(
        f"✅ *{label}* обновлено.\n\n"
        f"✏️ *Редактирование: {name_str}*\n\nВыберите поле для изменения:",
        parse_mode="Markdown",
        reply_markup=kb_pkg_edit_menu(pkg_id),
    )
    return PKG_EDIT_MENU


# ---------------------------------------------------------------------------
# PKG EDIT GROUPS
# ---------------------------------------------------------------------------


async def on_pkg_edit_groups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return PKG_EDIT_GROUPS
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "grp_done":
        pkg_id = ud.get("pkg_id")
        selected = ud.get("selected_groups", set())
        all_groups = ud.get("all_groups", [])
        if pkg_id is not None:
            groups_to_save = [g for g in all_groups if g["_id"] in selected]
            update_package(pkg_id, groups=groups_to_save)
            pkg = get_package(pkg_id)
            if pkg:
                await q.edit_message_text(
                    f"✏️ *Редактирование: {_esc(pkg['name'])}*\n\nВыберите поле для изменения:",
                    parse_mode="Markdown",
                    reply_markup=kb_pkg_edit_menu(pkg_id),
                )
        return PKG_EDIT_MENU

    if data.startswith("grp:"):
        _toggle_group(data, ctx)
        await q.edit_message_reply_markup(
            reply_markup=kb_groups(
                ud.get("all_groups", []),
                ud.get("selected_groups", set()),
            )
        )
        return PKG_EDIT_GROUPS

    return PKG_EDIT_GROUPS


# ---------------------------------------------------------------------------
# TOKENS LIST
# ---------------------------------------------------------------------------


async def on_tokens(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return TOKENS
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "back_main":
        await _show_main(update)
        return MAIN

    if data == "token_create":
        ud["token_new"] = {}
        await q.edit_message_text(
            "👤 *Создание токена — шаг 1 из 2*\n\nВведите *имя* клиента:",
            parse_mode="Markdown",
        )
        return TOKEN_CREATE_NAME

    if data.startswith("token_detail:"):
        token_id = int(data.split(":")[1])
        ud["token_id"] = token_id
        await _show_token_detail(update, token_id)
        return TOKEN_DETAIL

    await _show_tokens(update)
    return TOKENS


# ---------------------------------------------------------------------------
# TOKEN CREATE — sequential steps
# ---------------------------------------------------------------------------


async def on_token_create_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return TOKEN_CREATE_NAME
    ud = _ud(ctx)
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("❌ Имя не может быть пустым.")
        return TOKEN_CREATE_NAME
    ud.setdefault("token_new", {})["name"] = name
    await update.message.reply_text(
        "👤 *Создание токена — шаг 2 из 2*\n\n"
        "Введите *Telegram user\\_id* клиента (числовой ID),\n"
        "или нажмите «Пропустить» — его можно добавить позже:",
        parse_mode="Markdown",
        reply_markup=kb_skip("skip_tg"),
    )
    return TOKEN_CREATE_TG


async def on_token_create_tg_text(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    if not update.message:
        return TOKEN_CREATE_TG
    raw = (update.message.text or "").strip()
    try:
        int(raw)
    except ValueError:
        await update.message.reply_text(
            "❌ Telegram ID должен быть числом, например: `123456789`",
            parse_mode="Markdown",
        )
        return TOKEN_CREATE_TG
    _ud(ctx).setdefault("token_new", {})["tg_id"] = raw
    return await _finish_token_create(update, ctx)


async def on_token_create_tg_skip(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    _ud(ctx).setdefault("token_new", {})["tg_id"] = None
    return await _finish_token_create(update, ctx)


async def _finish_token_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ud = _ud(ctx)
    d = ud.get("token_new", {})
    token = create_client_token(d.get("name", ""), d.get("tg_id"))
    ud.pop("token_new", None)
    ud["token_id"] = token["id"]
    await _edit_or_reply(
        update, f"✅ *Токен создан!*\n\n{fmt_token(token)}", kb_token_detail(token)
    )
    return TOKEN_DETAIL


# ---------------------------------------------------------------------------
# TOKEN DETAIL
# ---------------------------------------------------------------------------


async def on_token_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return TOKEN_DETAIL
    await q.answer()
    data = q.data or ""
    ud = _ud(ctx)

    if data == "back_tokens":
        await _show_tokens(update)
        return TOKENS

    if data.startswith("token_topup:"):
        token_id = int(data.split(":")[1])
        ud["token_id"] = token_id
        token = get_client_token_by_id(token_id)
        if not token:
            await q.answer("Токен не найден", show_alert=True)
            return TOKEN_DETAIL
        await q.edit_message_text(
            f"💰 *Пополнение баланса*\n\n"
            f"Клиент: *{_esc(token['name'])}*\n"
            f"Текущий баланс: `{token['balance']} ₽`\n\n"
            f"Введите сумму пополнения:",
            parse_mode="Markdown",
        )
        return TOKEN_TOPUP_AMOUNT

    if data.startswith("token_toggle:"):
        token_id = int(data.split(":")[1])
        token = get_client_token_by_id(token_id)
        if not token:
            await q.answer("Токен не найден", show_alert=True)
            return TOKEN_DETAIL
        new_active = 0 if token["active"] else 1
        from src.database import get_db

        db = get_db()
        db.execute(
            "UPDATE client_tokens SET active=? WHERE id=?", (new_active, token_id)
        )
        db.commit()
        token = get_client_token_by_id(token_id)
        status = "✅ активирован" if new_active else "❌ деактивирован"
        await q.answer(f"Токен {status}")
        if token:
            await q.edit_message_text(
                fmt_token(token),
                parse_mode="Markdown",
                reply_markup=kb_token_detail(token),
            )
        return TOKEN_DETAIL

    return TOKEN_DETAIL


# ---------------------------------------------------------------------------
# TOKEN TOPUP AMOUNT (text input)
# ---------------------------------------------------------------------------


async def on_token_topup_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return TOKEN_TOPUP_AMOUNT
    ud = _ud(ctx)
    token_id = ud.get("token_id")
    if not token_id:
        await update.message.reply_text("⚠️ Ошибка состояния. Используйте /admin")
        return ConversationHandler.END

    try:
        amount = float((update.message.text or "").strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите положительное число, например: `500`", parse_mode="Markdown"
        )
        return TOKEN_TOPUP_AMOUNT

    new_balance = update_balance(token_id, amount)
    create_transaction(token_id, amount, "topup", "Admin top-up")
    token = get_client_token_by_id(token_id)
    if token:
        await update.message.reply_text(
            f"✅ Баланс пополнен на `{amount} ₽`\nНовый баланс: `{new_balance} ₽`\n\n"
            + fmt_token(token),
            parse_mode="Markdown",
            reply_markup=kb_token_detail(token),
        )
    return TOKEN_DETAIL


# ---------------------------------------------------------------------------
# STATS
# ---------------------------------------------------------------------------


async def on_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q is None:
        return STATS
    await q.answer()
    data = q.data or ""
    if data == "back_main":
        await _show_main(update)
        return MAIN
    if data == "stats_refresh":
        await _show_stats(update)
        return STATS
    return STATS


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app) -> None:
    _txt = filters.TEXT & ~filters.COMMAND

    conv = ConversationHandler(
        entry_points=[CommandHandler("admin", cmd_admin)],
        states={
            MAIN: [
                CallbackQueryHandler(on_main, pattern="^(packages|tokens|stats)$"),
            ],
            PACKAGES: [
                CallbackQueryHandler(
                    on_packages,
                    pattern="^(back_main|pkg_create|pkg_detail:\\d+)$",
                ),
            ],
            PKG_CREATE_NAME: [
                MessageHandler(_txt, on_pkg_create_name),
            ],
            PKG_CREATE_TRAFFIC: [
                MessageHandler(_txt, on_pkg_create_traffic),
            ],
            PKG_CREATE_DEVICES: [
                MessageHandler(_txt, on_pkg_create_devices),
            ],
            PKG_CREATE_DAYS: [
                MessageHandler(_txt, on_pkg_create_days),
            ],
            PKG_CREATE_PRICE: [
                MessageHandler(_txt, on_pkg_create_price),
            ],
            PKG_CREATE_DESC: [
                MessageHandler(_txt, on_pkg_create_desc_text),
                CallbackQueryHandler(on_pkg_create_desc_skip, pattern="^skip_desc$"),
            ],
            PKG_CREATE_GROUPS: [
                CallbackQueryHandler(
                    on_pkg_create_groups, pattern="^(grp:\\d+|grp_done)$"
                ),
            ],
            PKG_DETAIL: [
                CallbackQueryHandler(
                    on_pkg_detail,
                    pattern="^(back_packages|pkg_edit:\\d+|pkg_toggle:\\d+)$",
                ),
            ],
            PKG_EDIT_MENU: [
                CallbackQueryHandler(
                    on_pkg_edit_menu,
                    pattern="^(back_pkg_detail:\\d+|pkg_edit_groups:\\d+|pef:\\d+:\\w+)$",
                ),
            ],
            PKG_EDIT_VALUE: [
                MessageHandler(_txt, on_pkg_edit_value),
            ],
            PKG_EDIT_GROUPS: [
                CallbackQueryHandler(
                    on_pkg_edit_groups, pattern="^(grp:\\d+|grp_done)$"
                ),
            ],
            TOKENS: [
                CallbackQueryHandler(
                    on_tokens,
                    pattern="^(back_main|token_create|token_detail:\\d+)$",
                ),
            ],
            TOKEN_CREATE_NAME: [
                MessageHandler(_txt, on_token_create_name),
            ],
            TOKEN_CREATE_TG: [
                MessageHandler(_txt, on_token_create_tg_text),
                CallbackQueryHandler(on_token_create_tg_skip, pattern="^skip_tg$"),
            ],
            TOKEN_DETAIL: [
                CallbackQueryHandler(
                    on_token_detail,
                    pattern="^(back_tokens|token_topup:\\d+|token_toggle:\\d+)$",
                ),
            ],
            TOKEN_TOPUP_AMOUNT: [
                MessageHandler(_txt, on_token_topup_amount),
            ],
            STATS: [
                CallbackQueryHandler(on_stats, pattern="^(back_main|stats_refresh)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
