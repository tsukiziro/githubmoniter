import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.database.mongodb import get_cached_repositories, cache_user_repositories
from bot.keyboards.inline import (
    repo_list_keyboard,
    repo_action_keyboard,
    confirm_keyboard,
    back_cancel_keyboard
)
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

CREATE_REPO_NAME, CREATE_REPO_DESC, CREATE_REPO_VISIBILITY = range(3)

# --- Repository Listing & Pagination ---
async def show_repositories(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Displays paginated list of repositories."""
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Please connect your GitHub account first.")
        return

    client = GitHubAPIClient(token)
    try:
        repos = await client.list_repositories(page=1, per_page=100)
        await cache_user_repositories(telegram_id, repos)
    except GitHubAPIException as e:
        cached = await get_cached_repositories(telegram_id)
        if cached:
            repos = cached
        else:
            await safe_edit_or_reply(update, f"❌ Error fetching repositories: {e}")
            return

    per_page = 5
    total_repos = len(repos)
    total_pages = max(1, (total_repos + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_repos = repos[start_idx:start_idx + per_page]

    text = f"📁 <b>Your Repositories</b> ({total_repos} total)\nPage {page} of {total_pages}:"
    markup = repo_list_keyboard(page_repos, page, total_pages)

    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def repo_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for pagination buttons."""
    query = update.callback_query
    if query:
        await query.answer()
        page = int(query.data.split(":")[1])
        await show_repositories(update, context, page)

async def repo_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays details for a specific repository."""
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("repo_detail:")[1]
    else:
        return
    
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return

    owner, repo_name = repo_full_name.split("/")
    client = GitHubAPIClient(token)

    try:
        repo = await client.get_repository(owner, repo_name)
        visibility = "🔒 Private" if repo.get("private") else "🌐 Public"
        updated_at = repo.get("updated_at", "").replace("T", " ")[:16]
        
        detail_text = (
            f"📦 <b>{repo.get('full_name')}</b> ({visibility})\n"
            f"<i>{repo.get('description') or 'No description provided.'}</i>\n\n"
            f"⭐ <b>Stars:</b> {repo.get('stargazers_count')}\n"
            f"🍴 <b>Forks:</b> {repo.get('forks_count')}\n"
            f"👁️ <b>Watchers:</b> {repo.get('watchers_count')}\n"
            f"🐛 <b>Open Issues:</b> {repo.get('open_issues_count')}\n"
            f"🌿 <b>Default Branch:</b> {repo.get('default_branch')}\n"
            f"🕒 <b>Last Updated:</b> {updated_at} UTC\n\n"
            f"🔗 <a href='{repo.get('html_url')}'>Open in GitHub</a>"
        )
        
        markup = repo_action_keyboard(repo_full_name)
        await safe_edit_or_reply(update, text=detail_text, reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, f"❌ Error loading repo details: {e}")

# --- Repository Deletion ---
async def confirm_delete_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts user to confirm deletion of repository."""
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("repo_delete_confirm:")[1]
    else:
        return
    
    text = (
        f"⚠️ <b>Delete Repository Confirmation</b>\n\n"
        f"Are you sure you want to permanently delete <b>{repo_full_name}</b>?\n"
        f"<i>This action CANNOT be undone!</i>"
    )
    markup = confirm_keyboard("del_repo", repo_full_name)
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def handle_delete_repo_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes confirmation answer for deleting a repo."""
    query = update.callback_query
    if query:
        await query.answer()
        data_parts = query.data.split(":") # confirm_yes/no : del_repo : owner/repo
        decision = data_parts[0]
        repo_full_name = ":".join(data_parts[2:])
    else:
        return
    
    if decision == "confirm_no":
        await repo_detail_callback(update, context)
        return

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return

    owner, repo_name = repo_full_name.split("/")
    client = GitHubAPIClient(token)
    try:
        await client.delete_repository(owner, repo_name)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 All Repositories", callback_data="nav_repos")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(update, text=f"✅ Repository <b>{repo_full_name}</b> deleted successfully.", reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, text=f"❌ Failed to delete repository: {e}")

# --- Repository Creation Wizard ---
async def start_create_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts repo creation wizard."""
    query = update.callback_query
    text = "➕ <b>Create New Repository</b>\n\nPlease enter the repository name:"
    markup = back_cancel_keyboard("nav_repos")
    
    if query:
        await query.answer()
    
    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return CREATE_REPO_NAME

async def receive_repo_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repo_name = update.message.text.strip().replace(" ", "-")
    context.user_data["create_repo_name"] = repo_name
    
    await update.message.reply_text(
        f"Repository name set to: <b>{repo_name}</b>\n\nNow enter a description (or send /skip for none):",
        parse_mode="HTML"
    )
    return CREATE_REPO_DESC

async def receive_repo_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = "" if update.message.text == "/skip" else update.message.text.strip()
    context.user_data["create_repo_desc"] = desc
    
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Public", callback_data="vis_public"),
            InlineKeyboardButton("🔒 Private", callback_data="vis_private")
        ]
    ])
    
    await update.message.reply_text(
        "Select visibility for your new repository:",
        reply_markup=kb
    )
    return CREATE_REPO_VISIBILITY

async def receive_repo_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    is_private = (query.data == "vis_private") if query else False
    
    repo_name = context.user_data.get("create_repo_name")
    repo_desc = context.user_data.get("create_repo_desc", "")
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)

    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return ConversationHandler.END

    client = GitHubAPIClient(token)
    try:
        res = await client.create_repository(repo_name, repo_desc, is_private)
        full_name = res.get("full_name")
        html_url = res.get("html_url")
        
        text = (
            f"🎉 <b>Repository Created Successfully!</b>\n\n"
            f"<b>Name:</b> {full_name}\n"
            f"<b>Visibility:</b> {'🔒 Private' if is_private else '🌐 Public'}\n"
            f"<b>Direct Link:</b> {html_url}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open in GitHub", url=html_url)],
            [InlineKeyboardButton("📁 Manage Repo", callback_data=f"repo_detail:{full_name}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(update, text=text, reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, text=f"❌ Failed to create repository: {e}")
        
    return ConversationHandler.END

create_repo_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_create_repo, pattern="^nav_create_repo$"),
        CommandHandler("createrepo", start_create_repo)
    ],
    states={
        CREATE_REPO_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_repo_name)],
        CREATE_REPO_DESC: [
            CommandHandler("skip", receive_repo_desc),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_repo_desc)
        ],
        CREATE_REPO_VISIBILITY: [CallbackQueryHandler(receive_repo_visibility, pattern="^vis_")]
    },
    fallbacks=[
        CallbackQueryHandler(start_create_repo, pattern="^nav_repos$")
    ],
    per_message=False
)
