from typing import List, Dict, Any, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def auth_keyboard(oauth_url: str) -> InlineKeyboardMarkup:
    """Authentication options keyboard."""
    keyboard = [
        [InlineKeyboardButton("🔗 Connect with GitHub OAuth", url=oauth_url)],
        [InlineKeyboardButton("🔑 Use Personal Access Token", callback_data="auth_pat")]
    ]
    return InlineKeyboardMarkup(keyboard)

def main_dashboard_keyboard(notifications_on: bool = True) -> InlineKeyboardMarkup:
    """Main dashboard keyboard focusing strictly on Commit Scheduler."""
    keyboard = [
        [
            InlineKeyboardButton("⏰ Commit Scheduler", callback_data="nav_scheduler")
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="nav_settings"),
            InlineKeyboardButton("📖 User Guide", callback_data="nav_guide")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def scheduler_menu_keyboard(schedules: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """List of scheduled jobs and actions."""
    keyboard = []
    for sched in schedules:
        s_id = sched["schedule_id"]
        repo = sched["repo"]
        status = "▶️" if sched.get("status") == "active" else "⏸️"
        stype = sched.get("schedule_type", "custom")
        keyboard.append([InlineKeyboardButton(f"{status} {repo} ({stype})", callback_data=f"sched_view:{s_id}")])
        
    keyboard.append([
        InlineKeyboardButton("➕ New Schedule", callback_data="sched_create"),
        InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)

def schedule_detail_keyboard(schedule_id: str, is_active: bool) -> InlineKeyboardMarkup:
    """Schedule detail action controls."""
    toggle_text = "⏸️ Pause Schedule" if is_active else "▶️ Resume Schedule"
    toggle_action = "pause" if is_active else "resume"
    
    keyboard = [
        [
            InlineKeyboardButton(toggle_text, callback_data=f"sched_toggle:{schedule_id}:{toggle_action}"),
            InlineKeyboardButton("🗑 Delete Schedule", callback_data=f"sched_delete:{schedule_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Scheduler", callback_data="nav_scheduler")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def settings_keyboard() -> InlineKeyboardMarkup:
    """Settings menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🌐 Change Timezone", callback_data="settings_tz")
        ],
        [
            InlineKeyboardButton("🔌 Disconnect GitHub Account", callback_data="settings_disconnect")
        ],
        [
            InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def confirm_keyboard(action_code: str, target_id: str) -> InlineKeyboardMarkup:
    """Confirmation modal keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Confirm", callback_data=f"confirm_yes:{action_code}:{target_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"confirm_no:{action_code}:{target_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_cancel_keyboard(back_callback: str = "main_menu") -> InlineKeyboardMarkup:
    """Standard back and cancel buttons."""
    keyboard = [
        [
            InlineKeyboardButton("🔙 Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
