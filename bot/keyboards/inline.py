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
    """Main dashboard inline navigation grid."""
    notif_text = "🔔 Monitoring: ON" if notifications_on else "🔕 Monitoring: OFF"
    keyboard = [
        [
            InlineKeyboardButton("📁 Repositories", callback_data="nav_repos"),
            InlineKeyboardButton("➕ Create Repo", callback_data="nav_create_repo")
        ],
        [
            InlineKeyboardButton("📤 Push Files", callback_data="nav_push_file"),
            InlineKeyboardButton("🐛 Issues", callback_data="nav_issues")
        ],
        [
            InlineKeyboardButton("👥 Collaborators", callback_data="nav_collaborators"),
            InlineKeyboardButton("📊 Analytics", callback_data="nav_analytics")
        ],
        [
            InlineKeyboardButton("📅 Activity", callback_data="nav_activity"),
            InlineKeyboardButton("⏰ Scheduler", callback_data="nav_scheduler")
        ],
        [
            InlineKeyboardButton(notif_text, callback_data="toggle_monitoring"),
            InlineKeyboardButton("⚙️ Settings", callback_data="nav_settings")
        ],
        [
            InlineKeyboardButton("📖 User Guide & Tutorial", callback_data="nav_guide")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def repo_list_keyboard(repos: List[Dict[str, Any]], page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Paginated list of repositories."""
    keyboard = []
    
    for r in repos:
        name = r.get("full_name", r.get("name"))
        is_private = "🔒 " if r.get("private") else "🌐 "
        keyboard.append([InlineKeyboardButton(f"{is_private}{name}", callback_data=f"repo_detail:{name}")])
    
    # Pagination Row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"repo_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"repo_page:{page + 1}"))
    
    if len(nav_row) > 1:
        keyboard.append(nav_row)

    # Action row
    keyboard.append([
        InlineKeyboardButton("➕ Create Repo", callback_data="nav_create_repo"),
        InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)

def repo_action_keyboard(repo_full_name: str) -> InlineKeyboardMarkup:
    """Individual repository action controls."""
    keyboard = [
        [
            InlineKeyboardButton("🐛 Issues", callback_data=f"repo_issues:{repo_full_name}"),
            InlineKeyboardButton("➕ New Issue", callback_data=f"repo_new_issue:{repo_full_name}")
        ],
        [
            InlineKeyboardButton("👥 Collaborators", callback_data=f"repo_collabs:{repo_full_name}"),
            InlineKeyboardButton("📊 Traffic Analytics", callback_data=f"repo_analytics:{repo_full_name}")
        ],
        [
            InlineKeyboardButton("📤 Push File", callback_data=f"repo_push:{repo_full_name}"),
            InlineKeyboardButton("⏰ Schedule Commit", callback_data=f"repo_sched:{repo_full_name}")
        ],
        [
            InlineKeyboardButton("🗑 Delete Repository", callback_data=f"repo_delete_confirm:{repo_full_name}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Repositories", callback_data="nav_repos")
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

def issues_list_keyboard(repo_full_name: str, issues: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """List of open issues for a repository."""
    keyboard = []
    for issue in issues[:8]:
        num = issue.get("number")
        title = issue.get("title", "")[:25]
        keyboard.append([InlineKeyboardButton(f"#{num} {title}", callback_data=f"issue_detail:{repo_full_name}:{num}")])
    
    keyboard.append([
        InlineKeyboardButton("➕ Create Issue", callback_data=f"repo_new_issue:{repo_full_name}"),
        InlineKeyboardButton("🔙 Back to Repo", callback_data=f"repo_detail:{repo_full_name}")
    ])
    return InlineKeyboardMarkup(keyboard)

def issue_detail_keyboard(repo_full_name: str, issue_number: int, state: str) -> InlineKeyboardMarkup:
    """Issue detail action controls."""
    toggle_action = "close" if state == "open" else "reopen"
    toggle_text = "🔒 Close Issue" if state == "open" else "🔓 Reopen Issue"
    
    keyboard = [
        [
            InlineKeyboardButton(toggle_text, callback_data=f"issue_toggle:{repo_full_name}:{issue_number}:{toggle_action}"),
            InlineKeyboardButton("💬 Add Comment", callback_data=f"issue_add_comment:{repo_full_name}:{issue_number}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Issues", callback_data=f"repo_issues:{repo_full_name}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def scheduler_menu_keyboard(schedules: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """List of scheduled jobs and actions."""
    keyboard = []
    for sched in schedules:
        s_id = sched["schedule_id"]
        repo = sched["repo"]
        status = "▶️" if sched["status"] == "active" else "⏸️"
        keyboard.append([InlineKeyboardButton(f"{status} {repo} ({sched['schedule_type']})", callback_data=f"sched_view:{s_id}")])
        
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
            InlineKeyboardButton("🔔 Toggle Notifications", callback_data="toggle_monitoring"),
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

def back_cancel_keyboard(back_callback: str = "main_menu") -> InlineKeyboardMarkup:
    """Standard back and cancel buttons."""
    keyboard = [
        [
            InlineKeyboardButton("🔙 Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
