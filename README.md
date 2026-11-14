<p align="center">
  <img src="assets/banner.png" alt="GitHub Guardian Banner" width="100%">
</p>

<h1 align="center">🐙 GitHub Guardian</h1>

<p align="center">
  <strong>Production-Ready Telegram Bot for Automated GitHub Commit Scheduling, Profile Achievements Farming & Repository Automation</strong>
</p>

<p align="center">
  <a href="#-key-features">Key Features</a> •
  <a href="#-supported-github-achievements">Achievements</a> •
  <a href="#-bot-commands">Bot Commands</a> •
  <a href="#%EF%B8%8F-technology-stack">Tech Stack</a> •
  <a href="#-project-structure">Project Structure</a> •
  <a href="#-getting-started">Getting Started</a> •
  <a href="#%EF%B8%8F-configuration">Configuration</a> •
  <a href="#-deployment">Deployment</a> •
  <a href="#-security-architecture">Security</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Telegram_Bot-v20.8+-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="python-telegram-bot">
  <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/MongoDB-Motor_Async-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB">
  <img src="https://img.shields.io/badge/APScheduler-AsyncIO-FF6B6B?style=for-the-badge" alt="APScheduler">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License MIT">
</p>

---

## 📖 Overview

**GitHub Guardian** is a high-performance, asynchronous Telegram bot built with **Python 3.11+**, **python-telegram-bot (v20+)**, **FastAPI**, **Motor (MongoDB)**, and **APScheduler**.

It empowers developers to automate legitimate commit activity on schedule, unlock prestigious GitHub profile achievements in a controlled sandbox repository, and monitor user acquisition through a built-in admin suite—all seamlessly operated through interactive Telegram keyboards and inline dialogs.

---

## ✨ Key Features

### ⏰ 1. Advanced Commit Scheduler & Contribution Keeper
- **Automated Git Commits**: Schedule recurring, genuine updates to keep your activity graphs consistent and documentation logs maintained.
- **Dedicated Repository Isolation**: Prompts to auto-create and target a dedicated repository (e.g. `github-guardian-commits` or custom repo), keeping your production code untouched.
- **Flexible Scheduling Modes**:
  - **Daily Commits**: Set target commits per day with randomized distribution across daylight hours.
  - **Weekly Planner**: Customize commit targets per weekday (e.g. Mon: 5, Tue: 8, Wed: 10, Thu: 5, Fri: 8, Sat: 2, Sun: 1).
  - **Deep Green Mode**: Hourly scheduled commits (up to 24/day).
  - **Custom Start Times & Cron**: Pick specific run times (e.g., `09:00 AM`) or custom cron expressions.
- **Dynamic Content & Sha Resolution**: Automatic SHA detection with dynamic `{{TIMESTAMP}}` injection so GitHub always accepts new commits cleanly.
- **Job Lifecycle Controls**: View schedule status, pause, resume, edit, or delete scheduled jobs anytime.

### 🏆 2. GitHub Profile Achievements Farmer
- **10 Profile Badges Supported**: Unlock rare and official GitHub badges safely directly from Telegram.
- **⚡ One-Click "UNLOCK ALL"**: Trigger a full automation pipeline that unlocks all eligible badges in sequential order.
- **🛡️ Anti-Bot Pacing Shield**: Employs humanized delays (2.5s–4.5s random jitter) between API operations to protect your account against GitHub abuse flags and secondary rate limits.
- **Clean Sandbox Execution**: Operates exclusively in a dedicated public repository (`free-achievements`), preserving the hygiene of your primary repositories.
- **Live Terminal-Style Progress Bar**: Interactive spinner animation and real-time progress bar (`[■■■■□□□□□□] 40%`) delivered live to your Telegram chat.

### 🔐 3. Dual Authentication & Enterprise-Grade Security
- **GitHub OAuth 2.0 Flow**: Browser-based OAuth authorization with HMAC-SHA256 signed CSRF state tokens bound to the Telegram User ID.
- **Personal Access Token (PAT) Support**: Direct interactive PAT login fallback with **automatic message deletion** in Telegram to eliminate token leaks in chat history.
- **At-Rest Token Encryption**: All access tokens and PATs are encrypted using **Fernet (AES-128-CBC)** before being written to MongoDB.
- **Sensitive Log Redaction**: Custom logging filter automatically masks `ghp_`, `gho_`, `github_pat_`, and `Bearer` authorization headers in stdout.

### 👑 4. Admin Control Panel & Growth Analytics
- **Privileged Access**: Locked strictly to `ADMIN_ID`.
- **Live Metric Dashboard**: View total registered users, new signups in the last 24 hours, new signups in the last 7 days, and active/total commit schedules.
- **Data Export**: Export the entire user database to CSV with one click.
- **Global Broadcast**: Dispatch announcements and updates to all registered bot users with live broadcast statistics.

### 📢 5. Force-Subscription & Channel Verification
- **Channel Membership Gating**: Require users to join official channels/groups before accessing bot features via `MUST_JOIN_CHANNELS`.
- **In-Memory TTL Caching**: High-efficiency 60-second cache prevents Telegram rate limits during frequent membership checks.
- **Auto Join-Request Approver**: Built-in `ChatJoinRequestHandler` instantly approves pending channel/group join requests automatically.
- **Customizable Welcome Banners**: Rotates curated or custom banner URLs defined via `WELCOME_IMAGE_URLS`.

### ⚙️ 6. Timezone Awareness & Account Controls
- **Full Timezone Support**: Configure custom timezones (`pytz`) so scheduled jobs trigger precisely according to your local time.
- **Account Disconnect**: Safely unbind your GitHub account and delete encrypted credentials at any time.

---

## 🏆 Supported GitHub Achievements

| Badge | Achievement | How It Works |
| :---: | :--- | :--- |
| 🚀 | **Quickdraw** | Opens an issue in your dedicated repo and closes it within 5 minutes. |
| 🤠 | **YOLO** | Creates and merges a Pull Request without awaiting review. |
| 👥 | **Pair Extraordinaire** | Merges a commit co-authored with an assistant collaborator. |
| 🧠 | **Galaxy Brain** | Starts a Q&A discussion, replies, and accepts the answer as the resolution. |
| 🦈 | **Pull Shark** *(Bronze / Silver / Gold)* | Automates PR creation and merging across multiple pull requests. |
| ❤️ | **Heart on Sleeve** | Reacts to 10+ issues or pull requests with a heart (`❤️`) reaction. |
| 👀 | **Public Sighting** | Mentions your username in a public repository issue. |
| 💬 | **First Interaction** | Creates your first public comment interaction on an open issue. |
| 🌱 | **Open Source Heart** | Pushes contributions targeting the `hacktoberfest` repository topic. |
| 🌐 | **GitHub Pages** | Deploys a live website repository using the GitHub Pages deployment API. |

---

## 🤖 Bot Commands

| Command | Description | Access |
| :--- | :--- | :---: |
| `/start` | Launch the main dashboard & interactive navigation menu | Public |
| `/schedule` | Open the Commit Scheduler manager & schedule creation wizard | Authenticated |
| `/achievements` | Open the Free GitHub Profile Achievements & Badges catalog | Authenticated |
| `/settings` | Update user timezone (`pytz`) or disconnect account | Authenticated |
| `/guide` | View the step-by-step tutorial and feature guide | Public |
| `/help` | Quick usage tips and assistance | Public |
| `/admin` | Open the Admin Control Panel (stats, CSV export, broadcast) | Admin Only |

---

## 🛠️ Technology Stack

| Layer | Component | Details |
| :--- | :--- | :--- |
| **Language** | Python 3.11+ | Modern async/await architecture |
| **Telegram Framework** | `python-telegram-bot` (v20+) | High-throughput AsyncIO bot framework |
| **HTTP & API Client** | `httpx` | Async client for GitHub REST v3 & GraphQL APIs |
| **Web Server** | `FastAPI` + `Uvicorn` | OAuth 2.0 callback handling & health endpoints |
| **Database** | `Motor` + `PyMongo` | Asynchronous MongoDB driver for persistent state |
| **Task Scheduling** | `APScheduler` | Persistent AsyncIO job scheduler with timezone awareness |
| **Cryptography** | `cryptography` (Fernet) | AES-128-CBC at-rest encryption for access tokens |
| **Timezone Management** | `pytz` | Worldwide timezone conversion for commit schedules |

---

## 📁 Project Structure

```
github_monitor/
│
├── api/
│   └── oauth_callback.py          # FastAPI OAuth callback handler & health check
│
├── assets/
│   └── banner.png                 # GitHub Guardian banner graphic
│
├── bot/
│   ├── main.py                    # Application builder, command & callback routing
│   ├── database/
│   │   └── mongodb.py             # Motor async MongoDB operations, indexes & caching
│   ├── handlers/
│   │   ├── start.py               # /start, force-join check & user guide
│   │   ├── auth.py                # PAT manual authentication conversation
│   │   ├── scheduler.py           # Commit scheduler creation wizard & job controls
│   │   ├── achievements.py        # GitHub achievements catalog & live unlock execution
│   │   ├── settings.py            # Timezone selection & account disconnect
│   │   └── admin.py               # Admin dashboard, CSV export & global broadcast
│   ├── keyboards/
│   │   └── inline.py              # Dynamic inline keyboard builder layouts
│   ├── services/
│   │   ├── github_api.py          # Async GitHub REST & GraphQL API client (httpx)
│   │   ├── auth_service.py        # OAuth flow, HMAC CSRF signing & Fernet encryption
│   │   ├── achievement_service.py # Automated badge unlocker with anti-bot jitter
│   │   ├── encryption.py          # Fernet symmetric encryption / decryption helpers
│   │   ├── scheduler_service.py   # APScheduler job management & commit execution
│   │   └── monitoring_service.py  # Webhook processor & periodic fallback worker
│   └── utils/
│       └── helpers.py             # Safe message edit/reply & datetime helpers
│
├── free_achievements/             # Standalone achievements scripts & reference
│   ├── achievements.py
│   └── README.md
│
├── .env.example                   # Environment configuration template
├── Procfile                       # Process configuration for Heroku/Railway
├── render.yaml                    # Infrastructure-as-code blueprint for Render
├── requirements.txt               # Project Python package dependencies
├── run.py                         # Unified entry point (FastAPI + Telegram Bot)
└── README.md                      # Project documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python 3.11+** installed on your system.
- **MongoDB** running locally (`mongodb://localhost:27017`) or a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster.
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather).
- A **GitHub OAuth Application** (for OAuth login) or a Personal Access Token.

---

### 2. Clone and Setup Environment

```bash
# Clone the repository
git clone https://github.com/your-username/github_monitor.git
cd github_monitor

# Create and activate virtual environment
python -m venv venv

# On Windows:
venv\Scripts\activate
# On Linux / macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

### 3. Register a GitHub OAuth Application

1. Go to your GitHub account: **Settings** ➔ **Developer settings** ➔ **OAuth Apps** ➔ **New OAuth App**.
2. Fill out the application details:
   - **Application Name**: `GitHub Guardian Bot`
   - **Homepage URL**: `http://localhost:8000` (or your public deployment URL)
   - **Authorization callback URL**: `http://localhost:8000/auth/github/callback`
3. Click **Register application**.
4. Copy the **Client ID**.
5. Click **Generate a new client secret** and copy the **Client Secret**.

---

### 4. Generate Fernet Encryption Key

Generate a secure base64 Fernet key for token encryption:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Telegram Bot token from [@BotFather](https://t.me/BotFather) |
| `GITHUB_CLIENT_ID` | **Yes** | — | GitHub OAuth Application Client ID |
| `GITHUB_CLIENT_SECRET` | **Yes** | — | GitHub OAuth Application Client Secret |
| `GITHUB_REDIRECT_URI` | **Yes** | `http://localhost:8000/auth/github/callback` | OAuth callback endpoint URL |
| `APP_BASE_URL` | **Yes** | `http://localhost:8000` | Root public URL of your FastAPI server |
| `MONGODB_URI` | **Yes** | `mongodb://localhost:27017/github_guardian` | MongoDB connection URI (local or Atlas) |
| `MONGODB_DB_NAME` | No | `github_guardian` | MongoDB database name |
| `FERNET_KEY` | **Yes** | — | 32-byte url-safe base64 key for token encryption |
| `ADMIN_ID` | No | `1447828370` | Telegram numerical User ID of the bot administrator |
| `MUST_JOIN_CHANNELS` | No | — | Optional channel username or ID for force-sub verification |
| `WELCOME_IMAGE_URLS` | No | — | Comma-separated image URLs for random welcome banners |
| `STATE_SECRET` | No | Random salt | Secret key used for signing OAuth state CSRF tokens |
| `PORT` | No | `8000` | Web server port for OAuth callback and health check |

---

## 🏃 Running the Bot

### Local Development

Start both the FastAPI OAuth server and the Telegram bot polling process with a single command:

```bash
python run.py
```

You will see:
```text
======================================================
 🐙 GITHUB GUARDIAN BOT IS NOW ONLINE & RUNNING!
 --------------------------------------------------
 🚀 Telegram Polling: ACTIVE
 🌐 OAuth Server:     http://localhost:8000
 ⏰ APScheduler:      ACTIVE
======================================================
```

### Exposing Localhost for OAuth (Ngrok / Cloudflare Tunnel)

To test GitHub OAuth locally behind a NAT/firewall:

```bash
ngrok http 8000
```

Then update your `.env` and GitHub OAuth App callback URL:
```ini
APP_BASE_URL=https://your-ngrok-subdomain.ngrok-free.app
GITHUB_REDIRECT_URI=https://your-ngrok-subdomain.ngrok-free.app/auth/github/callback
```

---

## ☁️ Deployment

### Option 1: Render (Recommended)
This repository includes a [`render.yaml`](render.yaml) specification:
1. Push your repository to GitHub.
2. Link your repository in [Render Dashboard](https://dashboard.render.com).
3. Create a **Blueprint** or **Web Service**.
4. Configure your environment variables in the Render settings.
5. Deploy!

### Option 2: Heroku / Railway
Using the included [`Procfile`](Procfile):
```text
web: python run.py
```
1. Connect your GitHub repository to Railway or Heroku.
2. Set `PORT` and required environment variables in the project settings.
3. Deploy the service.

### Option 3: VPS / Linux Server (Systemd)

Create a systemd service file `/etc/systemd/system/github_guardian.service`:

```ini
[Unit]
Description=GitHub Guardian Bot
After=network.target mongodb.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/github_monitor
ExecStart=/home/ubuntu/github_monitor/venv/bin/python run.py
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/github_monitor/.env

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable github_guardian
sudo systemctl start github_guardian
```

---

## 🛡️ Security Architecture

1. **At-Rest Token Encryption**:
   Every GitHub token (OAuth token or Personal Access Token) is encrypted using AES-128-CBC via Fernet cryptography before being committed to MongoDB. Tokens are decrypted only in memory when executing API calls.
2. **Instant Message Deletion**:
   When authenticating via Personal Access Token in chat, the bot immediately deletes the user's incoming Telegram message to prevent credential retention in chat histories.
3. **CSRF State Validation**:
   The OAuth authorization URL embeds an HMAC-SHA256 signed `state` token bound to the specific Telegram user ID. Callback verification rejects invalid or tampered requests.
4. **Console Log Redaction**:
   A custom logging filter (`SensitiveDataFilter`) actively scrubs sensitive access tokens (`ghp_*`, `gho_*`, `github_pat_*`, and `Bearer` headers) from all terminal logs.
5. **Anti-Abuse API Rate Limiting**:
   Achievement unlock requests and automated commits implement humanized delay intervals and backoff pacing to ensure full compliance with GitHub's REST and GraphQL API guidelines.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

<div align="center">
  <sub>Built with ❤️ for developers seeking seamless GitHub automation and achievement tracking directly on Telegram.</sub>
</div>
