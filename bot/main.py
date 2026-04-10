import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, filters, ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, N8N_BASE_URL
import agents.qa as qa
import agents.router as router
import agents.builder as builder
import agents.interpreter as interpreter
import utils.n8n as n8n
from utils.model_config import (
    AVAILABLE_MODELS, get_user_model, set_user_model,
)
from agents.network import ALL_PRESETS, DEFAULT_PRESET_ID, run_network
from utils.memory import ensure_user_name, get_memory_context, update_task_history
from utils.media import transcribe_voice, extract_document, describe_image
from utils.billing import (
    TIER_CONFIG, get_user_key, provision_key, top_up_key, get_key_usage,
    check_and_reset_weekly_limits,
)
from utils.payments import TIERS, send_tier_invoice, send_topup_invoice, parse_payment_payload
from utils.workflows_db import (
    WorkflowRecord, add_workflow, get_user_workflows, update_workflow_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Per-user history: {user_id: [{"role": ..., "content": ...}, ...]}
_history: dict[int, list[dict]] = {}
MAX_HISTORY = 10  # messages kept per user (each exchange = 2 entries)

# Deduplication: track recently processed update IDs to prevent double-replies
_processed_updates: set[int] = set()
_MAX_PROCESSED = 200

# Per-user network preset selection (in-memory)
_user_network_preset: dict[int, str] = {}


def _is_duplicate(update: Update) -> bool:
    uid = update.update_id
    if uid in _processed_updates:
        logger.warning("Duplicate update_id=%s — skipping", uid)
        return True
    _processed_updates.add(uid)
    if len(_processed_updates) > _MAX_PROCESSED:
        to_remove = sorted(_processed_updates)[:_MAX_PROCESSED // 2]
        _processed_updates.difference_update(to_remove)
    logger.debug("Processing update_id=%s", uid)
    return False


def _get_history(user_id: int) -> list[dict]:
    return _history.get(user_id, [])


def _get_ask_kwargs(user_id: int) -> dict:
    """Build ask() kwargs with per-user model/provider/key overrides."""
    cfg = get_user_model(user_id)
    kwargs: dict = {"model_override": cfg["model"], "provider_override": cfg["provider"]}
    key_rec = get_user_key(user_id)
    if key_rec and not key_rec.get("disabled") and key_rec.get("key_value"):
        kwargs["user_api_key"] = key_rec["key_value"]
    return kwargs


def _update_history(user_id: int, user_message: str, assistant_reply: str) -> None:
    hist = _history.setdefault(user_id, [])
    hist.append({"role": "user", "content": user_message})
    hist.append({"role": "assistant", "content": assistant_reply})
    # Keep only the last MAX_HISTORY messages
    if len(hist) > MAX_HISTORY:
        _history[user_id] = hist[-MAX_HISTORY:]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_duplicate(update):
        return
    await update.message.reply_text(
        "Привет! Я AI Automation Builder. "
        "Описывай задачу или задавай вопросы — я помогу автоматизировать или отвечу на вопрос."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_duplicate(update):
        return
    await update.message.reply_text(
        "Что я умею:\n"
        "• Отвечать на вопросы об автоматизации задач\n"
        "• Создавать готовые автоматизации из текстового описания\n"
        "• Генерировать n8n workflows по шаблонам\n\n"
        "Доступные шаблоны:\n"
        "  — Telegram сообщения → Google Sheets\n"
        "  — Telegram бот с AI-ответами\n"
        "  — Форма/Webhook → Telegram уведомление\n\n"
        "Команды:\n"
        "/model — выбрать AI-модель\n"
        "/network — выбрать агентную сеть\n"
        "/services — подписка и управление автоматизациями\n"
        "/usage — сколько бюджета использовано (неделя / месяц)\n"
        "/topup [рубли] — пополнить LLM-бюджет\n"
        "/billing — текущий баланс\n\n"
        "Также можешь отправлять:\n"
        "🎤 Голосовые сообщения — распознаю и отвечу\n"
        "📄 Документы (PDF, Excel, DOCX) — извлеку текст\n"
        "📷 Фотографии — опишу что вижу\n\n"
        "Просто опиши задачу своими словами."
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show inline keyboard for model selection."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    current = get_user_model(user_id)

    def _btn(m: dict) -> InlineKeyboardButton:
        mark = " ✓" if m["provider"] == current["provider"] and m["model"] == current["model"] else ""
        return InlineKeyboardButton(f"{m['label']}{mark}", callback_data=f"model:{m['id']}")

    buttons = [_btn(m) for m in AVAILABLE_MODELS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    await update.message.reply_text(
        "Выбери модель для генерации ответов:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process inline button press for model selection."""
    if _is_duplicate(update):
        return
    query = update.callback_query
    await query.answer()

    model_id = query.data.removeprefix("model:")
    chosen = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)
    if not chosen:
        await query.edit_message_text("Неизвестная модель, попробуй ещё раз.")
        return

    user_id = update.effective_user.id
    set_user_model(user_id, chosen["provider"], chosen["model"])
    try:
        await query.edit_message_text(f"Модель переключена на {chosen['label']}.")
    except Exception:
        pass
    logger.info("User %s switched model to %s (%s)", user_id, chosen["model"], chosen["provider"])


async def cmd_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show inline keyboard for network preset selection."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    current_id = _user_network_preset.get(user_id, DEFAULT_PRESET_ID)

    rows: list[list[InlineKeyboardButton]] = []
    for preset in ALL_PRESETS.values():
        mark = " ✓" if preset["id"] == current_id else ""
        rows.append([InlineKeyboardButton(
            f"{preset['name']}{mark}",
            callback_data=f"network:{preset['id']}",
        )])

    await update.message.reply_text(
        "Выбери агентную сеть для сложных задач:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _progress_bar(pct: float, width: int = 10) -> str:
    """Return a Unicode progress bar string, e.g. '████░░░░░░ 42%'."""
    filled = min(width, round(pct / 100 * width))
    return f"{'█' * filled}{'░' * (width - filled)}  {pct:.0f}%"


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current LLM usage with progress bars (weekly + monthly)."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    key_rec = get_user_key(user_id)

    if not key_rec:
        await update.message.reply_text(
            "У тебя нет активной подписки.\n\nИспользуй /services чтобы выбрать тариф."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        usage_info = await get_key_usage(key_rec["key_hash"])
    except Exception as e:
        logger.error("Usage fetch error for user %s: %s", user_id, e)
        await update.message.reply_text("Не удалось получить данные. Попробуй позже.")
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Weekly usage = total usage since last reset snapshot
    usage_at_reset = key_rec.get("usage_at_last_reset", 0.0)
    weekly_used = max(0.0, usage_info["usage"] - usage_at_reset)
    weekly_limit = key_rec["weekly_limit"]
    weekly_pct = min(100.0, (weekly_used / weekly_limit * 100) if weekly_limit else 0.0)

    # Monthly usage = total usage against monthly budget
    monthly_used = usage_info["usage"]
    monthly_limit = key_rec["monthly_budget"]
    monthly_pct = min(100.0, (monthly_used / monthly_limit * 100) if monthly_limit else 0.0)

    # Days until weekly reset
    try:
        next_reset = datetime.fromisoformat(key_rec["next_weekly_reset"])
        days_left = max(0, (next_reset - now).days)
        reset_str = f"{days_left} дн." if days_left > 0 else "сегодня"
    except Exception:
        reset_str = "?"

    status = "⚠️ Заморожен (лимит недели)" if key_rec.get("disabled") else "✅ Активен"

    lines = [
        f"*Использование LLM*",
        f"",
        f"Тариф: {key_rec['tier'].capitalize()} · {status}",
        f"Сброс недели через: {reset_str}",
        f"",
        f"*Неделя*",
        f"`{_progress_bar(weekly_pct)}`",
        f"${weekly_used:.4f} из ${weekly_limit:.2f}",
        f"",
        f"*Месяц*",
        f"`{_progress_bar(monthly_pct)}`",
        f"${monthly_used:.4f} из ${monthly_limit:.2f}",
    ]

    if weekly_pct >= 90 or monthly_pct >= 90:
        lines.append(f"\nПополнить: /topup <stars>")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show subscription tier selection and deployed workflows."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    key_rec = get_user_key(user_id)

    # Build tier section
    if key_rec:
        tier_cfg = TIER_CONFIG.get(key_rec["tier"], {})
        price_rub = tier_cfg.get("rubles", "?")
        status_line = (
            f"Текущий тариф: *{key_rec['tier'].capitalize()}* ({price_rub} ₽/мес)\n"
            f"LLM-бюджет: ${key_rec['monthly_budget']:.2f}/мес · "
            f"Лимит недели: ${key_rec['weekly_limit']:.2f}\n"
            f"{'⚠️ Ключ заморожен (недельный лимит)' if key_rec.get('disabled') else '✅ Активен'}"
        )
    else:
        status_line = "У тебя ещё нет подписки."

    # Tier selection buttons
    tier_rows = []
    for tier in TIERS:
        mark = " ✓" if (key_rec and key_rec.get("tier") == tier["id"]) else ""
        tier_rows.append([InlineKeyboardButton(
            f"{tier['label']}{mark}", callback_data=f"buy_tier:{tier['id']}"
        )])

    # Deployed workflows
    workflows = get_user_workflows(user_id)
    wf_text = ""
    wf_rows = []
    if workflows:
        wf_text = "\n\n*Твои автоматизации:*"
        for wf in workflows[-10:]:  # last 10
            icon = "🟢" if wf.get("active") else "🔴"
            wf_text += f"\n{icon} {wf['name']} (#{wf['workflow_id']})"
            toggle_label = "Выкл" if wf.get("active") else "Вкл"
            wf_rows.append([InlineKeyboardButton(
                f"{toggle_label} #{wf['workflow_id']}",
                callback_data=f"wf_toggle:{wf['workflow_id']}",
            )])

    keyboard = InlineKeyboardMarkup(tier_rows + wf_rows)
    await update.message.reply_text(
        f"{status_line}{wf_text}\n\nВыбери тариф или управляй автоматизациями:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a top-up invoice. Usage: /topup [рубли]"""
    if _is_duplicate(update):
        return
    args = context.args or []
    chat_id = update.effective_chat.id

    if args:
        try:
            rubles = int(args[0])
            if rubles < 100:
                await update.message.reply_text("Минимальное пополнение — 100 ₽.")
                return
            await send_topup_invoice(context.bot, chat_id, rubles)
        except ValueError:
            await update.message.reply_text("Укажи сумму в рублях: /topup 500")
    else:
        await update.message.reply_text(
            "Выбери тариф для подписки или пополни баланс командой /topup <рубли>:"
        )
        for tier in TIERS:
            await send_tier_invoice(context.bot, chat_id, tier["id"])


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current billing status."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    key_rec = get_user_key(user_id)

    if not key_rec:
        await update.message.reply_text(
            "У тебя нет активной подписки.\n\nИспользуй /services чтобы выбрать тариф."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        usage = await get_key_usage(key_rec["key_hash"])
        remaining = usage["limit_remaining"]
        spent = usage["usage"]
        status = "⚠️ Заморожен" if key_rec.get("disabled") else "✅ Активен"
        await update.message.reply_text(
            f"*Биллинг*\n\n"
            f"Тариф: {key_rec['tier'].capitalize()}\n"
            f"Статус: {status}\n"
            f"Потрачено: ${spent:.4f}\n"
            f"Осталось: ${remaining:.4f}\n"
            f"Лимит недели: ${key_rec['weekly_limit']:.2f}\n\n"
            f"Пополнить: /topup <stars>",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Billing status error for user %s: %s", user_id, e)
        await update.message.reply_text("Не удалось получить данные биллинга. Попробуй позже.")


async def _handle_buy_tier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send invoice when user picks a tier."""
    if _is_duplicate(update):
        return
    query = update.callback_query
    await query.answer()

    tier_id = query.data.removeprefix("buy_tier:")
    chat_id = update.effective_chat.id
    try:
        await send_tier_invoice(context.bot, chat_id, tier_id)
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error("Send tier invoice error: %s", e)
        await query.edit_message_text("Ошибка при создании счёта, попробуй позже.")


async def _handle_wf_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a workflow active/inactive."""
    if _is_duplicate(update):
        return
    query = update.callback_query
    await query.answer()

    workflow_id = query.data.removeprefix("wf_toggle:")
    user_id = update.effective_user.id
    workflows = get_user_workflows(user_id)
    wf = next((w for w in workflows if w["workflow_id"] == workflow_id), None)

    if not wf:
        await query.edit_message_text("Автоматизация не найдена.")
        return

    n8n_ok = await n8n.health_check()
    new_active = not wf.get("active", True)

    if n8n_ok:
        if new_active:
            await n8n.activate(workflow_id)
        else:
            await n8n.deactivate(workflow_id)

    update_workflow_status(user_id, workflow_id, active=new_active)
    state = "активирована" if new_active else "деактивирована"
    try:
        await query.edit_message_text(
            f"{'🟢' if new_active else '🔴'} Автоматизация {wf['name']} {state}."
        )
    except Exception:
        pass


async def _handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Always approve pre-checkout queries (Stars payments)."""
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def _handle_successful_payment(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Provision or top-up user key after successful Stars payment."""
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    try:
        kind, value = parse_payment_payload(payload)
    except ValueError as e:
        logger.error("Unknown payment payload %r for user %s: %s", payload, user_id, e)
        await update.message.reply_text("Платёж получен, но не удалось его обработать. Напиши в поддержку.")
        return

    try:
        if kind == "tier":
            tier_id = str(value)
            key_rec = await provision_key(user_id, tier_id)
            await update.message.reply_text(
                f"✅ Подписка *{tier_id.capitalize()}* активирована!\n\n"
                f"LLM-бюджет: ${key_rec['monthly_budget']:.2f}/мес\n"
                f"Недельный лимит: ${key_rec['weekly_limit']:.2f}\n\n"
                f"Используй /billing для проверки баланса.",
                parse_mode="Markdown",
            )
        elif kind == "topup":
            rubles = int(value)
            key_rec = await top_up_key(user_id, rubles)
            from config import RUB_PER_USD, LLM_SHARE
            added_usd = round((rubles / RUB_PER_USD) * LLM_SHARE, 4)
            await update.message.reply_text(
                f"✅ Баланс пополнен на ${added_usd:.4f}!\n\n"
                f"Новый бюджет: ${key_rec['monthly_budget']:.4f}\n\n"
                f"Используй /billing для проверки баланса.",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error("Payment processing error for user %s: %s", user_id, e)
        await update.message.reply_text(
            "Платёж получен, но возникла ошибка активации. Попробуй /billing через минуту."
        )


async def _weekly_limit_checker(_app: Application) -> None:
    """Background task: run every hour to reset weekly limits."""
    while True:
        await asyncio.sleep(3600)
        try:
            await check_and_reset_weekly_limits()
            logger.debug("Weekly limit checker ran")
        except Exception as e:
            logger.error("Weekly limit checker error: %s", e)


async def _post_init(app: Application) -> None:
    asyncio.create_task(_weekly_limit_checker(app))


async def _handle_network_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process inline button press for network preset selection."""
    if _is_duplicate(update):
        return
    query = update.callback_query
    await query.answer()

    preset_id = query.data.removeprefix("network:")
    preset = ALL_PRESETS.get(preset_id)
    if not preset:
        await query.edit_message_text("Неизвестный пресет, попробуй ещё раз.")
        return

    user_id = update.effective_user.id
    _user_network_preset[user_id] = preset_id
    try:
        await query.edit_message_text(
            f"Агентная сеть: {preset['name']}\n{preset['description']}"
        )
    except Exception:
        pass
    logger.info("User %s switched network preset to %s", user_id, preset_id)


async def _handle_deep_task(
    update: Update,
    user_id: int,
    user_message: str,
    ask_kwargs: dict,
) -> str:
    """Run an agent network and return the final result. Updates progress in-place."""
    preset_id = _user_network_preset.get(user_id, DEFAULT_PRESET_ID)
    preset = ALL_PRESETS[preset_id]

    progress_msg = await update.message.reply_text(
        f"Агентная сеть: {preset['name']}\n\nЗапускаю..."
    )

    async def progress_cb(status: str) -> None:
        try:
            await progress_msg.edit_text(
                f"Агентная сеть: {preset['name']}\n\n{status}"
            )
        except Exception:
            pass

    result = await run_network(preset, user_message, progress_cb, ask_kwargs=ask_kwargs)

    try:
        await progress_msg.edit_text(
            f"Агентная сеть: {preset['name']} — готово ✓"
        )
    except Exception:
        pass

    return result


async def _handle_automation(
    user_id: int,
    user_message: str,
    history: list[dict],
    ask_kwargs: dict,
) -> str:
    """Interpret → Build → (optionally) Deploy to n8n. Returns user-facing reply."""
    interp_result = await interpreter.extract(user_message, history, ask_kwargs=ask_kwargs)
    task = interp_result["task"]

    build_result = await builder.generate(task, user_message, ask_kwargs=ask_kwargs)

    # Try n8n deploy if available
    n8n_available = await n8n.health_check()
    deploy_info = None
    if n8n_available and build_result["template_id"] not in ("error", "custom"):
        try:
            deploy_info = await n8n.deploy(build_result["workflow_json"])
            await n8n.activate(deploy_info["id"])
        except Exception as e:
            logger.warning("n8n deploy failed: %s", e)

    # Record deployed workflow
    if deploy_info:
        from datetime import datetime, timezone
        add_workflow(user_id, WorkflowRecord(
            workflow_id=deploy_info["id"],
            name=deploy_info.get("name", build_result["template_id"]),
            template_id=build_result["template_id"],
            created_at=datetime.now(timezone.utc).isoformat(),
            active=True,
            n8n_url=f"{N8N_BASE_URL}/workflow/{deploy_info['id']}" if N8N_BASE_URL else "",
        ))

    # Compose reply
    reply = build_result["summary"]
    if deploy_info:
        reply += f"\n\nАвтоматизация активирована в n8n (ID: {deploy_info['id']})."
    else:
        reply += "\n\nАвтоматизация готова к запуску. Подключи n8n чтобы активировать её."

    # Show guides for any PENDING placeholders
    guides = builder.get_pending_guides(build_result["filled_placeholders"])
    if guides:
        reply += f"\n\n{guides}"

    logger.info(
        "Workflow for user %s (template=%s):\n%s",
        user_id,
        build_result["template_id"],
        json.dumps(build_result["workflow_json"], ensure_ascii=False, indent=2),
    )
    return reply


_TG_MAX_LENGTH = 4096


def _md_to_html(text: str) -> str:
    """Convert common Markdown to Telegram-compatible HTML."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")

    text = re.sub(r"```\w*\n(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    return text


async def _send_long_message(update: Update, text: str) -> None:
    """Convert Markdown to HTML, split into <=4096-char chunks and send."""
    html = _md_to_html(text)

    async def _send_chunk(chunk: str) -> None:
        try:
            await update.message.reply_text(chunk, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(chunk)

    if len(html) <= _TG_MAX_LENGTH:
        await _send_chunk(html)
        return
    while html:
        if len(html) <= _TG_MAX_LENGTH:
            await _send_chunk(html)
            break
        split_at = html.rfind("\n", 0, _TG_MAX_LENGTH)
        if split_at < _TG_MAX_LENGTH // 4:
            split_at = _TG_MAX_LENGTH
        chunk = html[:split_at]
        html = html[split_at:].lstrip("\n")
        await _send_chunk(chunk)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    user_message = update.message.text
    history = _get_history(user_id)

    ensure_user_name(user_id, update.effective_user.first_name or "")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    ask_kwargs = _get_ask_kwargs(user_id)
    mem_ctx = get_memory_context(user_id)

    try:
        route = await router.classify(user_message, history, ask_kwargs=ask_kwargs)
        logger.info(
            "Route for user %s: %s (%.2f) — %s",
            user_id, route["type"], route["confidence"], route["intent"],
        )

        if route["type"] == "deep_task":
            reply = await _handle_deep_task(update, user_id, user_message, ask_kwargs)

        elif route["type"] == "automation":
            reply = await _handle_automation(user_id, user_message, history, ask_kwargs)
            update_task_history(user_id, route["intent"])

        elif route["type"] == "hybrid":
            qa_reply, auto_reply = await asyncio.gather(
                qa.answer(user_message, history, ask_kwargs=ask_kwargs, memory_context=mem_ctx),
                _handle_automation(user_id, user_message, history, ask_kwargs),
            )
            reply = f"{qa_reply}\n\n---\n{auto_reply}"
            update_task_history(user_id, route["intent"])

        else:  # "qa" — default / fallback
            reply = await qa.answer(user_message, history, ask_kwargs=ask_kwargs, memory_context=mem_ctx)

        _update_history(user_id, user_message, reply)
        await _send_long_message(update, reply)

    except Exception as e:
        logger.error("Processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Произошла ошибка, попробуй ещё раз.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download voice message, transcribe with Whisper, feed into normal pipeline."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    ensure_user_name(user_id, update.effective_user.first_name or "")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        voice = update.message.voice or update.message.audio
        tg_file = await voice.get_file()

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        await update.message.reply_text("🎤 Распознаю голос...")
        text = await transcribe_voice(tmp_path)

        import os
        os.unlink(tmp_path)

        if not text.strip():
            await update.message.reply_text("Не удалось распознать речь, попробуй ещё раз.")
            return

        await update.message.reply_text(f"📝 Распознано: {text[:200]}{'...' if len(text) > 200 else ''}")

        ask_kwargs = _get_ask_kwargs(user_id)
        mem_ctx = get_memory_context(user_id)
        history = _get_history(user_id)

        route = await router.classify(text, history, ask_kwargs=ask_kwargs)
        logger.info("Voice route for user %s: %s (%.2f)", user_id, route["type"], route["confidence"])

        if route["type"] == "deep_task":
            reply = await _handle_deep_task(update, user_id, text, ask_kwargs)
        elif route["type"] == "automation":
            reply = await _handle_automation(user_id, text, history, ask_kwargs)
            update_task_history(user_id, route["intent"])
        else:
            reply = await qa.answer(text, history, ask_kwargs=ask_kwargs, memory_context=mem_ctx)

        _update_history(user_id, f"[Голос] {text}", reply)
        await _send_long_message(update, reply)

    except Exception as e:
        logger.error("Voice processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Ошибка обработки голосового сообщения.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download document, extract text, send as context to QA."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    ensure_user_name(user_id, update.effective_user.first_name or "")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        doc = update.message.document
        file_name = doc.file_name or "file"
        ext = Path(file_name).suffix.lower()

        if ext not in (".pdf", ".xlsx", ".xls", ".docx", ".doc"):
            await update.message.reply_text(
                f"Формат {ext} пока не поддерживается. Поддерживаю: PDF, Excel, DOCX."
            )
            return

        tg_file = await doc.get_file()

        import tempfile as _tf
        with _tf.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        await update.message.reply_text(f"📄 Извлекаю текст из {file_name}...")
        text = await extract_document(tmp_path)

        import os
        os.unlink(tmp_path)

        caption = update.message.caption or ""
        ask_kwargs = _get_ask_kwargs(user_id)
        mem_ctx = get_memory_context(user_id)

        if caption:
            user_prompt = f"{caption}\n\nСодержимое документа ({file_name}):\n{text}"
        else:
            user_prompt = f"Пользователь отправил документ ({file_name}). Кратко опиши содержимое и предложи, чем можешь помочь.\n\nСодержимое:\n{text}"

        history = _get_history(user_id)
        reply = await qa.answer(user_prompt, history, ask_kwargs=ask_kwargs, memory_context=mem_ctx)

        _update_history(user_id, f"[Документ: {file_name}] {caption}", reply)
        await _send_long_message(update, reply)

    except Exception as e:
        logger.error("Document processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Ошибка обработки документа.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo, describe via vision model."""
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    ensure_user_name(user_id, update.effective_user.first_name or "")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo = update.message.photo[-1]  # highest resolution
        tg_file = await photo.get_file()

        import tempfile as _tf
        with _tf.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        await update.message.reply_text("📷 Анализирую изображение...")
        ask_kwargs = _get_ask_kwargs(user_id)
        description = await describe_image(tmp_path, ask_kwargs=ask_kwargs)

        import os
        os.unlink(tmp_path)

        caption = update.message.caption or ""
        if caption:
            mem_ctx = get_memory_context(user_id)
            history = _get_history(user_id)
            user_prompt = f"{caption}\n\nОписание изображения:\n{description}"
            reply = await qa.answer(user_prompt, history, ask_kwargs=ask_kwargs, memory_context=mem_ctx)
        else:
            reply = description

        history_entry = f"[Пользователь отправил фото. Описание: {description}]"
        if caption:
            history_entry += f" Вопрос: {caption}"
        _update_history(user_id, history_entry, reply)
        await _send_long_message(update, reply)

    except Exception as e:
        logger.error("Photo processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Ошибка обработки изображения.")


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("network", cmd_network))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("billing", cmd_billing))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CallbackQueryHandler(_handle_model_callback, pattern=r"^model:"))
    app.add_handler(CallbackQueryHandler(_handle_network_callback, pattern=r"^network:"))
    app.add_handler(CallbackQueryHandler(_handle_buy_tier_callback, pattern=r"^buy_tier:"))
    app.add_handler(CallbackQueryHandler(_handle_wf_toggle_callback, pattern=r"^wf_toggle:"))
    app.add_handler(PreCheckoutQueryHandler(_handle_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, _handle_successful_payment))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()