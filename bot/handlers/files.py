import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.database.mongodb import get_cached_repositories
from bot.keyboards.inline import back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

SELECT_REPO, ENTER_FILE_PATH, ENTER_BRANCH, ENTER_COMMIT_MSG = range(4)

async def handle_incoming_file(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Entry point when user sends a document/photo or changes repo selection page."""
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Please connect your GitHub account before uploading files.")
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await query.answer()
        if query.data.startswith("push_page:"):
            page = int(query.data.split(":")[1])

    if update.message:
        document = update.message.document
        photo = update.message.photo
        
        if document:
            file_obj = await document.get_file()
            file_bytes = await file_obj.download_as_bytearray()
            suggested_name = document.file_name or "file.txt"
            context.user_data["upload_bytes"] = bytes(file_bytes)
            context.user_data["suggested_filename"] = suggested_name
        elif photo:
            file_obj = await photo[-1].get_file()
            file_bytes = await file_obj.download_as_bytearray()
            suggested_name = f"photo_{file_obj.file_unique_id}.jpg"
            context.user_data["upload_bytes"] = bytes(file_bytes)
            context.user_data["suggested_filename"] = suggested_name

    suggested_name = context.user_data.get("suggested_filename", "file.txt")

    # Fetch user repos to select
    repos = await get_cached_repositories(telegram_id)
    if not repos:
        client = GitHubAPIClient(token)
        try:
            repos = await client.list_repositories(page=1, per_page=100)
        except Exception as e:
            await safe_edit_or_reply(update, f"❌ Error fetching repositories: {e}")
            return ConversationHandler.END

    per_page = 8
    total_repos = len(repos)
    total_pages = max(1, (total_repos + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_repos = repos[start_idx:start_idx + per_page]

    keyboard = []
    for r in page_repos:
        r_name = r.get("full_name")
        keyboard.append([InlineKeyboardButton(r_name, callback_data=f"push_repo:{r_name}")])
    
    # Pagination
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"push_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"push_page:{page + 1}"))
    
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="main_menu")])

    markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📥 Received <b>{suggested_name}</b>.\n\n"
        f"Select the target repository to commit this file to (Page {page}/{total_pages}):"
    )

    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return SELECT_REPO

async def select_repo_for_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("push_repo:")[1]
        context.user_data["push_target_repo"] = repo_full_name

    suggested = context.user_data.get("suggested_filename", "file.txt")
    target = context.user_data.get("push_target_repo", "")
    
    text = (
        f"Selected repository: <b>{target}</b>\n\n"
        f"Please enter the destination path in the repository (e.g. <code>src/{suggested}</code> or send /default for <code>{suggested}</code>):"
    )
    await safe_edit_or_reply(update, text=text)
    return ENTER_FILE_PATH

async def enter_file_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    suggested = context.user_data.get("suggested_filename", "file.txt")
    
    file_path = suggested if msg_text == "/default" else msg_text
    context.user_data["push_file_path"] = file_path

    await update.message.reply_text(
        f"Destination path: <code>{file_path}</code>\n\n"
        f"Enter the target branch (or send /default to use the repo default branch):",
        parse_mode="HTML"
    )
    return ENTER_BRANCH

async def enter_branch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    branch = None if msg_text == "/default" else msg_text
    context.user_data["push_branch"] = branch

    await update.message.reply_text(
        f"Branch: <b>{branch or 'Default Branch'}</b>\n\n"
        f"Enter the commit message (or send /default for 'Add {context.user_data.get('suggested_filename')} via GitHub Guardian'):",
        parse_mode="HTML"
    )
    return ENTER_COMMIT_MSG

async def enter_commit_msg_and_push(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    suggested = context.user_data.get("suggested_filename", "file.txt")
    commit_msg = f"Add {suggested} via GitHub Guardian" if msg_text == "/default" else msg_text

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await update.message.reply_text("Session expired.")
        return ConversationHandler.END

    repo_full_name = context.user_data["push_target_repo"]
    owner, repo_name = repo_full_name.split("/")
    file_path = context.user_data["push_file_path"]
    branch = context.user_data.get("push_branch")
    file_bytes = context.user_data["upload_bytes"]

    status_msg = await update.message.reply_text("⏳ Pushing file to GitHub...")

    client = GitHubAPIClient(token)
    try:
        # Check if file exists to get SHA for update
        sha = None
        try:
            file_info = await client.get_file_contents(owner, repo_name, file_path, ref=branch)
            if isinstance(file_info, dict) and "sha" in file_info:
                sha = file_info["sha"]
        except GitHubAPIException as ge:
            if ge.status_code != 404:
                raise ge

        res = await client.create_or_update_file(
            owner=owner,
            repo=repo_name,
            path=file_path,
            content_bytes=file_bytes,
            commit_message=commit_msg,
            branch=branch,
            sha=sha
        )

        commit_url = res.get("commit", {}).get("html_url", "")
        action = "Updated" if sha else "Created"
        
        await status_msg.edit_text(
            f"🎉 <b>File {action} Successfully!</b>\n\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Path:</b> <code>{file_path}</code>\n"
            f"<b>Commit:</b> {commit_msg}\n"
            f"<b>Direct Link:</b> {commit_url}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View Commit on GitHub", url=commit_url)],
                [InlineKeyboardButton("📁 View Repo", callback_data=f"repo_detail:{repo_full_name}")],
                [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
            ])
        )
    except GitHubAPIException as e:
        await status_msg.edit_text(f"❌ Failed to push file: {e}")
        
    return ConversationHandler.END

file_push_conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Document.ALL | filters.PHOTO, handle_incoming_file),
        CallbackQueryHandler(handle_incoming_file, pattern="^nav_push_file$")
    ],
    states={
        SELECT_REPO: [
            CallbackQueryHandler(handle_incoming_file, pattern="^push_page:"),
            CallbackQueryHandler(select_repo_for_file, pattern="^push_repo:")
        ],
        ENTER_FILE_PATH: [
            CommandHandler("default", enter_file_path),
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_file_path)
        ],
        ENTER_BRANCH: [
            CommandHandler("default", enter_branch),
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_branch)
        ],
        ENTER_COMMIT_MSG: [
            CommandHandler("default", enter_commit_msg_and_push),
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_commit_msg_and_push)
        ]
    },
    fallbacks=[CallbackQueryHandler(handle_incoming_file, pattern="^main_menu$")],
    per_message=False
)
