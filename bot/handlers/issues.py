import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.keyboards.inline import issues_list_keyboard, issue_detail_keyboard, back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

CREATE_ISSUE_TITLE, CREATE_ISSUE_BODY = range(2)
ADD_COMMENT_BODY = 10

# --- List & View Issues ---
async def list_repo_issues(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        data = query.data.split("repo_issues:")[1]
        repo_full_name = data
    else:
        return
        
    owner, repo_name = repo_full_name.split("/")
    
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return

    client = GitHubAPIClient(token)
    try:
        issues = await client.list_issues(owner, repo_name, state="open")
        issues = [i for i in issues if "pull_request" not in i]
        
        if not issues:
            text = f"🐛 <b>Issues for {repo_full_name}</b>\n\nNo open issues found!"
        else:
            text = f"🐛 <b>Open Issues for {repo_full_name}</b> ({len(issues)} total):"
            
        markup = issues_list_keyboard(repo_full_name, issues)
        await safe_edit_or_reply(update, text=text, reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, f"❌ Error fetching issues: {e}")

async def show_issue_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        parts = query.data.split("issue_detail:")[1].split(":")
        repo_full_name = parts[0]
        issue_num = int(parts[1])
    else:
        return
        
    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return

    client = GitHubAPIClient(token)
    try:
        issue = await client.get_issue(owner, repo_name, issue_num)
        state_icon = "🟢 Open" if issue.get("state") == "open" else "🔴 Closed"
        created_at = issue.get("created_at", "").replace("T", " ")[:16]
        
        text = (
            f"🐛 <b>Issue #{issue.get('number')}: {issue.get('title')}</b> ({state_icon})\n"
            f"<b>Author:</b> @{issue.get('user', {}).get('login')}\n"
            f"<b>Created:</b> {created_at} UTC\n\n"
            f"<b>Description:</b>\n{issue.get('body') or '<i>No description provided.</i>'}\n\n"
            f"💬 <b>Comments:</b> {issue.get('comments', 0)}\n"
            f"🔗 <a href='{issue.get('html_url')}'>View on GitHub</a>"
        )
        
        markup = issue_detail_keyboard(repo_full_name, issue_num, issue.get("state"))
        await safe_edit_or_reply(update, text=text, reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, f"❌ Error fetching issue details: {e}")

async def toggle_issue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closes or reopens an issue."""
    query = update.callback_query
    if query:
        await query.answer()
        parts = query.data.split("issue_toggle:")[1].split(":")
        repo_full_name = parts[0]
        issue_num = int(parts[1])
        new_state = "closed" if parts[2] == "close" else "open"
    else:
        return

    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return

    client = GitHubAPIClient(token)
    try:
        await client.update_issue_state(owner, repo_name, issue_num, new_state)
        action_verb = "closed" if new_state == "closed" else "reopened"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🐛 View Issues", callback_data=f"repo_issues:{repo_full_name}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(
            update,
            text=f"✅ Issue #{issue_num} in <b>{repo_full_name}</b> has been {action_verb}.",
            reply_markup=markup
        )
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, f"❌ Failed to update issue status: {e}")

# --- Create Issue Wizard ---
async def start_create_issue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("repo_new_issue:")[1]
        context.user_data["issue_target_repo"] = repo_full_name

    target = context.user_data.get("issue_target_repo", "")
    text = f"➕ <b>Create Issue in {target}</b>\n\nPlease enter the issue title:"
    markup = back_cancel_keyboard(f"repo_detail:{target}")
    
    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return CREATE_ISSUE_TITLE

async def receive_issue_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    context.user_data["issue_title"] = title

    await update.message.reply_text(
        f"Title: <b>{title}</b>\n\nNow enter the issue description/body (or send /skip for none):",
        parse_mode="HTML"
    )
    return CREATE_ISSUE_BODY

async def receive_issue_body_and_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = "" if update.message.text == "/skip" else update.message.text.strip()
    repo_full_name = context.user_data["issue_target_repo"]
    title = context.user_data["issue_title"]
    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await update.message.reply_text("Session expired.")
        return ConversationHandler.END

    client = GitHubAPIClient(token)
    try:
        issue = await client.create_issue(owner, repo_name, title, body)
        num = issue.get("number")
        url = issue.get("html_url")
        
        await update.message.reply_text(
            f"🎉 <b>Issue #{num} Created Successfully!</b>\n\n"
            f"<b>Title:</b> {title}\n"
            f"<b>Direct Link:</b> {url}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Issue on GitHub", url=url)],
                [InlineKeyboardButton("🐛 View Issues", callback_data=f"repo_issues:{repo_full_name}")],
                [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
            ])
        )
    except GitHubAPIException as e:
        await update.message.reply_text(f"❌ Failed to create issue: {e}")

    return ConversationHandler.END

create_issue_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_create_issue, pattern="^repo_new_issue:")],
    states={
        CREATE_ISSUE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_issue_title)],
        CREATE_ISSUE_BODY: [
            CommandHandler("skip", receive_issue_body_and_create),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_issue_body_and_create)
        ]
    },
    fallbacks=[CallbackQueryHandler(list_repo_issues, pattern="^repo_issues:")],
    per_message=False
)

# --- Add Comment Wizard ---
async def start_add_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        parts = query.data.split("issue_add_comment:")[1].split(":")
        repo_full_name = parts[0]
        issue_num = int(parts[1])
        context.user_data["comment_target_repo"] = repo_full_name
        context.user_data["comment_target_issue"] = issue_num

    repo = context.user_data.get("comment_target_repo", "")
    num = context.user_data.get("comment_target_issue", "")

    text = f"💬 <b>Add Comment to Issue #{num}</b>\n\nPlease enter your comment text:"
    markup = back_cancel_keyboard(f"issue_detail:{repo}:{num}")
    
    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return ADD_COMMENT_BODY

async def receive_comment_and_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment_text = update.message.text.strip()
    repo_full_name = context.user_data["comment_target_repo"]
    issue_num = context.user_data["comment_target_issue"]
    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await update.message.reply_text("Session expired.")
        return ConversationHandler.END

    client = GitHubAPIClient(token)
    try:
        res = await client.add_issue_comment(owner, repo_name, issue_num, comment_text)
        url = res.get("html_url")
        
        await update.message.reply_text(
            f"✅ <b>Comment Added to Issue #{issue_num}!</b>\n\n"
            f"<b>Direct Link:</b> {url}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View Comment on GitHub", url=url)],
                [InlineKeyboardButton("🐛 View Issue", callback_data=f"issue_detail:{repo_full_name}:{issue_num}")],
                [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
            ])
        )
    except GitHubAPIException as e:
        await update.message.reply_text(f"❌ Failed to add comment: {e}")

    return ConversationHandler.END

add_comment_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_add_comment, pattern="^issue_add_comment:")],
    states={
        ADD_COMMENT_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_comment_and_post)]
    },
    fallbacks=[CallbackQueryHandler(show_issue_detail, pattern="^issue_detail:")],
    per_message=False
)
