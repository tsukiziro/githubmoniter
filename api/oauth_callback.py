import logging
from fastapi import FastAPI, Request, HTTPException, Query, Header
from fastapi.responses import HTMLResponse
from bot.services.auth_service import verify_state, exchange_code_for_token, complete_oauth_login, get_user_decrypted_token
from bot.services.monitoring_service import process_github_webhook_event
from bot.keyboards.inline import main_dashboard_keyboard
from bot.database.mongodb import get_user

logger = logging.getLogger(__name__)

app = FastAPI(title="GitHub Guardian OAuth & Webhook API")

@app.get("/")
async def health_check():
    return {"status": "online", "app": "GitHub Guardian API"}

@app.get("/auth/github/callback", response_class=HTMLResponse)
async def github_oauth_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None)
):
    """GitHub OAuth callback endpoint handling token exchange and user session creation."""
    if error:
        logger.warning(f"OAuth Authorization error from GitHub: {error_description or error}")
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px; background-color: #0d1117; color: #f0f6fc;">
                    <h1 style="color: #f85149;">❌ Authorization Cancelled</h1>
                    <p>{error_description or 'GitHub authorization was declined.'}</p>
                    <p>You can close this window and try again in Telegram.</p>
                </body>
            </html>
            """,
            status_code=400
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required code or state parameter.")

    # 1. Validate state parameter (CSRF protection)
    telegram_id = verify_state(state)
    if not telegram_id:
        logger.warning(f"CSRF warning: Invalid state parameter '{state}'.")
        return HTMLResponse(
            content="""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px; background-color: #0d1117; color: #f0f6fc;">
                    <h1 style="color: #f85149;">⚠️ Security Warning (Invalid State)</h1>
                    <p>CSRF verification failed. Please return to Telegram and initiate login again.</p>
                </body>
            </html>
            """,
            status_code=403
        )

    try:
        # 2. Exchange authorization code for GitHub access token
        access_token = await exchange_code_for_token(code)

        # 3. Validate token, fetch profile, encrypt, and save user in DB
        user_doc = await complete_oauth_login(telegram_id, access_token)
        username = user_doc["github_username"]

        # 4. Notify user via Telegram Bot
        bot_app = getattr(request.app.state, "bot_app", None)
        if bot_app and hasattr(bot_app, "bot"):
            token, user = await get_user_decrypted_token(telegram_id)
            if token and user:
                dashboard_text = (
                    f"🎉 <b>Authentication Successful!</b>\n\n"
                    f"Connected as <b>@{username}</b> via GitHub OAuth.\n"
                    f"You now have access to repository management, file commits, issues, and monitoring!"
                )
                try:
                    await bot_app.bot.send_message(
                        chat_id=telegram_id,
                        text=dashboard_text,
                        parse_mode="HTML",
                        reply_markup=main_dashboard_keyboard(user.get("notifications", True))
                    )
                except Exception as ex:
                    logger.error(f"Failed to send Telegram notification to user {telegram_id}: {ex}")

        return HTMLResponse(
            content=f"""
            <html>
                <head><title>GitHub Guardian Connected</title></head>
                <body style="font-family: sans-serif; text-align: center; padding-top: 60px; background-color: #0d1117; color: #c9d1d9;">
                    <div style="max-width: 500px; margin: 0 auto; background-color: #161b22; padding: 30px; border-radius: 12px; border: 1px solid #30363d;">
                        <h1 style="color: #238636; margin-bottom: 10px;">🎉 Connection Successful!</h1>
                        <p style="font-size: 1.1em;">Your GitHub account <strong>@{username}</strong> is now connected to GitHub Guardian.</p>
                        <p style="color: #8b949e;">You may close this browser tab and return to Telegram.</p>
                    </div>
                </body>
            </html>
            """
        )

    except Exception as e:
        logger.error(f"Error completing OAuth login for user {telegram_id}: {e}")
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px; background-color: #0d1117; color: #f0f6fc;">
                    <h1 style="color: #f85149;">❌ Authentication Error</h1>
                    <p>{str(e)}</p>
                    <p>Please try again in Telegram.</p>
                </body>
            </html>
            """,
            status_code=500
        )

@app.post("/webhooks/github")
async def github_webhook_handler(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event")
):
    """Listens for incoming GitHub repository webhooks and dispatches Telegram notifications."""
    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header.")

    payload = await request.json()
    bot_app = getattr(request.app.state, "bot_app", None)
    
    if bot_app:
        await process_github_webhook_event(bot_app, x_github_event, payload)

    return {"status": "processed", "event": x_github_event}
