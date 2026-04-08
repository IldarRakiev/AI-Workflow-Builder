import asyncio
import json
import logging
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN
import agents.qa as qa
import agents.router as router
import agents.builder as builder
import agents.interpreter as interpreter
import utils.n8n as n8n
from utils.model_config import (
    AVAILABLE_MODELS, get_user_model, set_user_model,
)
from agents.network import ALL_PRESETS, DEFAULT_PRESET_ID, run_network

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
    """Build ask() kwargs with per-user model/provider overrides."""
    cfg = get_user_model(user_id)
    return {"model_override": cfg["model"], "provider_override": cfg["provider"]}


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
        "/network — выбрать агентную сеть\n\n"
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_duplicate(update):
        return
    user_id = update.effective_user.id
    user_message = update.message.text
    history = _get_history(user_id)

    # Show typing indicator immediately
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    ask_kwargs = _get_ask_kwargs(user_id)

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

        elif route["type"] == "hybrid":
            qa_reply, auto_reply = await asyncio.gather(
                qa.answer(user_message, history, ask_kwargs=ask_kwargs),
                _handle_automation(user_id, user_message, history, ask_kwargs),
            )
            reply = f"{qa_reply}\n\n---\n{auto_reply}"

        else:  # "qa" — default / fallback
            reply = await qa.answer(user_message, history, ask_kwargs=ask_kwargs)

        _update_history(user_id, user_message, reply)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error("Processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Произошла ошибка, попробуй ещё раз.")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("network", cmd_network))
    app.add_handler(CallbackQueryHandler(_handle_model_callback, pattern=r"^model:"))
    app.add_handler(CallbackQueryHandler(_handle_network_callback, pattern=r"^network:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()