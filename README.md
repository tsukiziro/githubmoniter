# 🐙 GitHub Guardian - Telegram Repository Manager & Monitoring Bot

**GitHub Guardian** is a production-grade Telegram Bot built with Python 3.11+ that allows developers and team leads to manage, monitor, update, and automate their GitHub repositories directly through Telegram.

---

## ✨ Features

- 🔐 **Dual Security Authentication**:
  - **GitHub OAuth 2.0 Flow**: Secure state parameter signed per user to prevent CSRF attacks.
  - **Fine-Grained Personal Access Tokens (PAT)**: Interactive PAT prompt with **automatic message deletion** in Telegram to prevent token exposure.
  - **At-Rest Encryption**: All tokens are encrypted using **Fernet symmetric encryption** derived from your secret key. Plaintext tokens are never logged or stored.
- 📁 **Repository Management**:
  - Create public and private repositories.
  - List user repositories with smooth inline pagination.
  - View repository stars, forks, watchers, open issues, default branch, and last update timestamp.
  - Safely delete repositories with an inline confirmation modal.
- 📤 **Push Files via Telegram**:
  - Send code, documents, text files, or photos directly in chat.
  - Interactive selection of target repository, destination file path, target branch, and commit message.
  - Automatic SHA detection for updating existing files vs creating new files.
- 🐛 **Issue Management**:
  - List open issues in any repository.
  - Interactive wizard to create new issues with titles and descriptions.
  - One-click close/reopen toggle for issues.
  - Post comments directly onto GitHub issues.
- 👥 **Collaborator Management**:
  - Invite collaborators with explicit permission roles (`pull`, `push`, `admin`, `triage`).
  - List current collaborators and roles.
  - Remove collaborators with permission-aware error handling.
- ⏰ **Scheduled Automated Commits**:
  - Schedule legitimate recurring updates (e.g. automated status logs, documentation updates).
  - Configure frequency: daily, weekly, or custom standard 5-part cron expressions.
  - Managed by **APScheduler** backed by MongoDB persistence.
  - Full management controls: list, pause, resume, edit, and delete schedules.
- 📅 **Activity Calendar & Dashboard**:
  - Contribution and event statistics powered by GitHub REST API.
  - Push event breakdowns and commit counts.
- 👁️ **Repository Traffic Analytics**:
  - Views, unique visitors, clones, unique cloners, stars, forks, and watchers display.
- 🔔 **Real-Time Monitoring & Webhooks**:
  - FastAPI server receiving webhooks for issue events, comments, pull requests, push events, workflow failures, and releases.
  - Fallback periodic polling worker for active activity monitoring.

---

## 🛠️ Technology Stack

- **Language**: Python 3.11+
- **Bot Framework**: `python-telegram-bot` (v20+ Async)
- **API Client**: `httpx` (Async HTTP)
- **OAuth & Webhook Server**: `FastAPI` + `Uvicorn`
- **Database**: `Motor` / `PyMongo` (Async MongoDB)
- **Task Scheduler**: `APScheduler` (`AsyncIOScheduler`)
- **Encryption**: `cryptography` (Fernet)
- **Environment**: `python-dotenv`

---

## 📁 Project Structure

```
github_guardian/
│
├── bot/
│   ├── main.py                    # Main Telegram bot initialization & handlers
│   ├── handlers/
│   │   ├── start.py               # /start command & main dashboard
│   │   ├── auth.py                # PAT authentication conversation handler
│   │   ├── repositories.py        # Repo listing, creation & deletion
│   │   ├── files.py               # Telegram file upload -> GitHub push handler
│   │   ├── issues.py              # Issue creation, listing, closing & comments
│   │   ├── collaborators.py       # Invite, list & remove collaborators
│   │   ├── analytics.py           # Activity calendar & repo traffic statistics
│   │   ├── scheduler.py           # Scheduled commit management
│   │   └── settings.py            # Account settings, timezone & disconnect
│   │
│   ├── services/
│   │   ├── github_api.py          # Async GitHub REST API Client (httpx)
│   │   ├── auth_service.py        # OAuth flow, PAT validation & token management
│   │   ├── encryption.py          # Fernet token encryption & decryption
│   │   ├── scheduler_service.py   # APScheduler job persistence & execution
│   │   └── monitoring_service.py  # Webhook processor & periodic fallback worker
│   │
│   ├── database/
│   │   └── mongodb.py             # Motor async database connection & helpers
│   │
│   └── keyboards/
│       └── inline.py              # Dynamic inline keyboard builders
│
├── api/
│   └── oauth_callback.py          # FastAPI OAuth callback & Webhook router
│
├── requirements.txt
├── .env.example
├── README.md
└── run.py                         # Root entry point launching Uvicorn & Bot
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
- Python 3.11 or higher
- MongoDB running locally (`mongodb://localhost:27017`) or MongoDB Atlas URI
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- A GitHub OAuth Application

### 2. Install Dependencies

```bash
git clone https://github.com/your-username/github_guardian.git
cd github_guardian
python -m venv venv

# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

---

## 🔑 Registering a GitHub OAuth Application

1. Go to your GitHub account **Settings** ➔ **Developer settings** ➔ **OAuth Apps** ➔ **New OAuth App**.
2. Fill out the application details:
   - **Application Name**: `GitHub Guardian Bot`
   - **Homepage URL**: `http://localhost:8000` (or your public domain / Ngrok URL)
   - **Authorization callback URL**: `http://localhost:8000/auth/github/callback`
3. Click **Register application**.
4. Copy the **Client ID**.
5. Click **Generate a new client secret** and copy the **Client Secret**.

---

## ⚙️ Environment Variables Configuration

Create a `.env` file in the project root based on `.env.example`:

```ini
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyZ

# GitHub OAuth Application Configuration
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
GITHUB_REDIRECT_URI=http://localhost:8000/auth/github/callback

# Database Configuration
MONGODB_URI=mongodb://localhost:27017/github_guardian

# Security & Encryption Configuration
# Generate key using: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=your_generated_fernet_key_here

# Webhook & OAuth Base URL
APP_BASE_URL=http://localhost:8000
```

---

## 🏃 Running locally

To start both the **FastAPI OAuth server** and the **Telegram Bot polling worker**:

```bash
python run.py
```

### Exposing for Webhooks & OAuth (Using Ngrok)

If running locally behind NAT, use [Ngrok](https://ngrok.com/) to expose port 8000:

```bash
ngrok http 8000
```

Then update your `.env` and GitHub OAuth App callback URL to:
`https://your-ngrok-subdomain.ngrok-free.app/auth/github/callback`

---

## 🛡️ Security Architecture

1. **At-Rest Token Encryption**:
   GitHub tokens (OAuth access tokens & PATs) are encrypted using AES-128-CBC via `cryptography.fernet.Fernet`. The key is stored safely in `FERNET_KEY`.
2. **Auto-Deletion of Telegram Messages**:
   When users send a Personal Access Token in chat during PAT login, the bot immediately attempts to delete the message via Telegram API to minimize exposure in chat logs.
3. **CSRF State Verification**:
   OAuth URLs contain an HMAC-SHA256 signed `state` parameter linked to the Telegram User ID, ensuring callback requests cannot be spoofed.
4. **Sensitive Data Log Redacting**:
   Custom log filters automatically mask `ghp_`, `gho_`, `github_pat_`, and `Bearer` headers in stdout.
