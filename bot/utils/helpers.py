import logging
from telegram import Update

logger = logging.getLogger(__name__)

async def safe_edit_or_reply(update: Update, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """
    Safely edits a Telegram message whether it is a photo message (caption), 
    text message, or sends a new message if editing fails.
    """
    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)
            return
        except Exception:
            try:
                await query.edit_message_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
                return
            except Exception:
                try:
                    await query.message.reply_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)
                    try:
                        await query.delete_message()
                    except Exception:
                        pass
                    return
                except Exception:
                    pass

    if update.effective_message:
        await update.effective_message.reply_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)
