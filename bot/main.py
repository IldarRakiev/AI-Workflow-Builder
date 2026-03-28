import asyncio
import json
import logging
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import TELEGRAM_BOT_TOKEN
import agents.qa as qa
import agents.router as router
import agents.builder as builder
import agents.interpreter as interpreter
import utils.n8n as n8n

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Per-user history: {user_id: [{"role": ..., "content": ...}, ...]}
_history: dict[int, list[dict]] = {}
MAX_HISTORY = 10  # messages kept per user (each exchange = 2 entries)


def _get_history(user_id: int) -> list[dict]:
    return _history.get(user_id, [])


def _update_history(user_id: int, user_message: str, assistant_reply: str) -> None:
    hist = _history.setdefault(user_id, [])
    hist.append({"role": "user", "content": user_message})
    hist.append({"role": "assistant", "content": assistant_reply})
    # Keep only the last MAX_HISTORY messages
    if len(hist) > MAX_HISTORY:
        _history[user_id] = hist[-MAX_HISTORY:]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я AI Automation Builder. "
        "Описывай задачу или задавай вопросы — я помогу автоматизировать или отвечу на вопрос."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Что я умею:\n"
        "• Отвечать на вопросы об автоматизации задач\n"
        "• Создавать готовые автоматизации из текстового описания\n"
        "• Генерировать n8n workflows по шаблонам\n\n"
        "Доступные шаблоны:\n"
        "  — Telegram сообщения → Google Sheets\n"
        "  — Telegram бот с AI-ответами\n"
        "  — Форма/Webhook → Telegram уведомление\n\n"
        "Просто опиши задачу своими словами."
    )


async def _handle_automation(
    user_id: int,
    user_message: str,
    history: list[dict],
) -> str:
    """Interpret → Build → (optionally) Deploy to n8n. Returns user-facing reply."""
    interp_result = await interpreter.extract(user_message, history)
    task = interp_result["task"]

    build_result = await builder.generate(task, user_message)

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
    user_id = update.effective_user.id
    user_message = update.message.text
    history = _get_history(user_id)

    # Show typing indicator immediately
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        route = await router.classify(user_message, history)
        logger.info(
            "Route for user %s: %s (%.2f) — %s",
            user_id, route["type"], route["confidence"], route["intent"],
        )

        if route["type"] == "automation":
            reply = await _handle_automation(user_id, user_message, history)

        elif route["type"] == "hybrid":
            qa_reply, auto_reply = await asyncio.gather(
                qa.answer(user_message, history),
                _handle_automation(user_id, user_message, history),
            )
            reply = f"{qa_reply}\n\n---\n{auto_reply}"

        else:  # "qa" — default / fallback
            reply = await qa.answer(user_message, history)

        _update_history(user_id, user_message, reply)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error("Processing error for user %s: %s", user_id, e)
        await update.message.reply_text("Произошла ошибка, попробуй ещё раз.")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()