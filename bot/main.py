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
from utils.memory import ensure_user_name, get_memory_context, update_task_history
from utils.media import transcribe_voice, extract_document, describe_image

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
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("network", cmd_network))
    app.add_handler(CallbackQueryHandler(_handle_model_callback, pattern=r"^model:"))
    app.add_handler(CallbackQueryHandler(_handle_network_callback, pattern=r"^network:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()