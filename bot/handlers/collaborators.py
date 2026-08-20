import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.keyboards.inline import back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

INVITE_COLLAB_USERNAME, INVITE_COLLAB_PERM = range(2)

async def list_collaborators_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists collaborators for a repository."""
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("repo_collabs:")[1]
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
        collabs = await client.list_collaborators(owner, repo_name)
        
        text = f"👥 <b>Collaborators for {repo_full_name}</b> ({len(collabs)} total):\n\n"
        keyboard = []
        
        for c in collabs:
            c_username = c.get("login")
            role = c.get("role_name", c.get("permissions", {}))
            text += f"• <b>@{c_username}</b> ({role})\n"
            keyboard.append([InlineKeyboardButton(f"❌ Remove @{c_username}", callback_data=f"remove_collab:{repo_full_name}:{c_username}")])
            
        keyboard.append([
            InlineKeyboardButton("➕ Invite Collaborator", callback_data=f"invite_collab:{repo_full_name}"),
            InlineKeyboardButton("🔙 Back to Repo", callback_data=f"repo_detail:{repo_full_name}")
        ])
        
        await safe_edit_or_reply(update, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    except GitHubAPIException as e:
        if e.status_code == 403:
            await safe_edit_or_reply(
                update,
                text=f"❌ <b>Insufficient Permissions</b>\n\nYour GitHub token does not have admin access to view/manage collaborators on <b>{repo_full_name}</b>."
            )
        else:
            await safe_edit_or_reply(update, text=f"❌ Error fetching collaborators: {e}")

async def remove_collaborator_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a collaborator from repository."""
    query = update.callback_query
    if query:
        await query.answer()
        parts = query.data.split("remove_collab:")[1].split(":")
        repo_full_name = parts[0]
        target_username = parts[1]
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
        await client.remove_collaborator(owner, repo_name, target_username)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Collaborators", callback_data=f"repo_collabs:{repo_full_name}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(
            update,
            text=f"✅ Collaborator <b>@{target_username}</b> removed from <b>{repo_full_name}</b>.",
            reply_markup=markup
        )
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, text=f"❌ Failed to remove collaborator: {e}")

# --- Invite Collaborator Wizard ---
async def start_invite_collaborator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("invite_collab:")[1]
        context.user_data["collab_target_repo"] = repo_full_name

    target = context.user_data.get("collab_target_repo", "")
    text = f"➕ <b>Invite Collaborator to {target}</b>\n\nPlease enter the GitHub username to invite:"
    markup = back_cancel_keyboard(f"repo_collabs:{target}")

    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return INVITE_COLLAB_USERNAME

async def receive_collab_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    context.user_data["collab_username"] = username

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Read (pull)", callback_data="perm_pull"),
            InlineKeyboardButton("Write (push)", callback_data="perm_push")
        ],
        [
            InlineKeyboardButton("Admin", callback_data="perm_admin"),
            InlineKeyboardButton("Triage", callback_data="perm_triage")
        ]
    ])

    await update.message.reply_text(
        f"Inviting <b>@{username}</b>.\nSelect permission level:",
        parse_mode="HTML",
        reply_markup=kb
    )
    return INVITE_COLLAB_PERM

async def receive_collab_perm_and_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        perm = query.data.split("perm_")[1]
    else:
        perm = "push"

    repo_full_name = context.user_data["collab_target_repo"]
    target_username = context.user_data["collab_username"]
    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return ConversationHandler.END

    client = GitHubAPIClient(token)
    try:
        res = await client.add_collaborator(owner, repo_name, target_username, permission=perm)
        invite_url = res.get("html_url", "")
        
        text = (
            f"🎉 <b>Invitation Sent!</b>\n\n"
            f"Invited <b>@{target_username}</b> as collaborator to <b>{repo_full_name}</b> with <b>{perm}</b> permission.\n\n"
            f"<b>Direct Link:</b> {invite_url}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 View Invitation on GitHub", url=invite_url)],
            [InlineKeyboardButton("👥 Collaborators", callback_data=f"repo_collabs:{repo_full_name}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(update, text=text, reply_markup=markup)
    except GitHubAPIException as e:
        if e.status_code == 403:
            await safe_edit_or_reply(update, text=f"❌ <b>Permission Denied:</b> You must have admin rights on the repository to invite collaborators.")
        else:
            await safe_edit_or_reply(update, text=f"❌ Failed to invite collaborator: {e}")

    return ConversationHandler.END

invite_collab_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_invite_collaborator, pattern="^invite_collab:")],
    states={
        INVITE_COLLAB_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_collab_username)],
        INVITE_COLLAB_PERM: [CallbackQueryHandler(receive_collab_perm_and_invite, pattern="^perm_")]
    },
    fallbacks=[CallbackQueryHandler(list_collaborators_callback, pattern="^repo_collabs:")],
    per_message=False
)
