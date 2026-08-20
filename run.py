import asyncio
import logging
import os
import re
import sys
import warnings
import uvicorn
from dotenv import load_dotenv

# Suppress PTBUserWarning and third-party deprecation warnings
warnings.filterwarnings("ignore")

# Load environment variables from .env file if available
load_dotenv()

# Silence noisy third-party loggers
for logger_name in ["httpx", "httpcore", "apscheduler", "telegram", "telegram.ext", "uvicorn", "uvicorn.access", "uvicorn.error", "asyncio"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# Custom Sensitive Filter to prevent tokens/secrets from appearing in logs
class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive tokens and keys from log records."""
    PATTERNS = [
        (re.compile(r'ghp_[a-zA-Z0-9]{36}'), 'ghp_REDACTED'),
        (re.compile(r'gho_[a-zA-Z0-9]{36}'), 'gho_REDACTED'),
        (re.compile(r'github_pat_[a-zA-Z0-9_]{82}'), 'github_pat_REDACTED'),
        (re.compile(r'access_token=[a-zA-Z0-9_]+'), 'access_token=REDACTED'),
        (re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]+'), 'Bearer REDACTED'),
    ]

    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern, replacement in self.PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True

# Configure clean logging format
handler = logging.StreamHandler(sys.stdout)
handler.addFilter(SensitiveDataFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[handler]
)
logger = logging.getLogger("github_guardian")

from api.oauth_callback import app as fastapi_app
from bot.main import build_application
from bot.services.scheduler_service import init_scheduler

async def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is not set in .env file.")

    # Initialize Scheduler
    scheduler = None
    try:
        scheduler = await init_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler initialization failed: {e}")

    # Build Telegram Bot application
    bot_app = await build_application()

    # Share bot application instance with FastAPI app context
    fastapi_app.state.bot_app = bot_app

    # Setup Uvicorn web server for OAuth callback & GitHub webhooks
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        loop="asyncio"
    )
    server = uvicorn.Server(config)

    # Launch bot and web server concurrently
    async with bot_app:
        await bot_app.start()
        if bot_app.updater:
            await bot_app.updater.start_polling()

        print("\n" + "=" * 54)
        print(" 🐙 GITHUB GUARDIAN BOT IS NOW ONLINE & RUNNING!")
        print(" --------------------------------------------------")
        print(" 🚀 Telegram Polling: ACTIVE")
        print(f" 🌐 OAuth Server:     http://localhost:{port}")
        print(" ⏰ APScheduler:      ACTIVE")
        print("=" * 54 + "\n")

        try:
            await server.serve()
        except asyncio.CancelledError:
            pass
        finally:
            if bot_app.updater and bot_app.updater.running:
                await bot_app.updater.stop()
            await bot_app.stop()
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
            logger.info("GitHub Guardian services shut down gracefully.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 GitHub Guardian stopped.")
