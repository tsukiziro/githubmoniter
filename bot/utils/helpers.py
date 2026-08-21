import logging
import pytz
from datetime import datetime, timezone
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

def format_user_datetime(dt_or_iso, user_tz_str: str = "Asia/Kolkata") -> str:
    """
    Converts a UTC datetime or ISO string to the user's local timezone (defaulting to Asia/Kolkata / IST).
    Example output: '2026-08-21 10:25:53 AM (IST)'
    """
    if not user_tz_str:
        user_tz_str = "Asia/Kolkata"
        
    try:
        target_tz = pytz.timezone(user_tz_str)
    except Exception:
        target_tz = pytz.timezone("Asia/Kolkata")

    if not dt_or_iso:
        return "N/A"

    if isinstance(dt_or_iso, str):
        try:
            clean_str = dt_or_iso.replace("Z", "+00:00")
            if "T" in clean_str:
                dt = datetime.fromisoformat(clean_str)
            else:
                dt = datetime.strptime(clean_str[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return dt_or_iso
    elif isinstance(dt_or_iso, datetime):
        dt = dt_or_iso
    else:
        return str(dt_or_iso)

    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)

    local_dt = dt.astimezone(target_tz)
    return local_dt.strftime("%Y-%m-%d %I:%M:%S %p (%Z)")
