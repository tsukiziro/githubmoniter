<div align="center">

# 🚀 GitHub Achievement Farmer
### *(w4simg Custom Edition)*

*A powerful, automated Python script designed to seamlessly unlock various GitHub profile achievements with minimal manual effort.*

[![Python](https://img.shields.io/badge/Python-3.x-blue?style=flat-square&logo=python)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

</div>

---

## 🌟 Supported Achievements

| Achievement | Description |
| :--- | :--- |
| **🚀 Quickdraw** | Opens and closes an issue within 5 minutes. |
| **🤠 YOLO** | Merges a Pull Request without any reviews. |
| **👥 Pair Extraordinaire** | Co-authors a merged commit. |
| **🧠 Galaxy Brain** | Creates a Q&A discussion and marks an answer as accepted. |
| **🦈 Pull Shark** *(Gold/Silver/Bronze)* | Automatically opens and merges up to 128 Pull Requests. |
| **❤️ Heart on Sleeve** | Reacts to 10+ issues or Pull Requests with a ❤️. |
| **👀 Public Sighting** | Mentions your username in a public issue. |
| **💬 First Interaction** | Posts your first comment on an issue. |
| **🌱 Open Source Heart** | Creates a PR in a repository tagged with the `hacktoberfest` topic. |
| **🌐 GitHub Pages** | Automatically publishes a basic website from a repository. |

---

## ⚙️ How It Works

1. **Authentication**: Uses a GitHub Personal Access Token to authenticate on your behalf via the official GitHub REST and GraphQL APIs.
2. **Dummy Repository**: Automatically generates a dedicated dummy repository (e.g., `free-achievments`) where all automated activity takes place, keeping your real profile clean!
3. **Simulated Activity**: Depending on your selection, it rapidly simulates human activity—pushing commits, opening issues, and merging pull requests.
4. **Built-in Integrity Protection**: The script features a self-checksumming core. It dynamically validates its own logic at runtime to ensure it hasn't been tampered with or corrupted.

---

## 🛠️ Requirements
* **Python 3.x** installed on your system.
* The `requests` Python library (`pip install requests`).
* A **GitHub Personal Access Token (Classic)** with the `repo` and `read:user` scopes.

---

<details>
<summary><b>🔑 How to Create a GitHub Personal Access Token (PAT)</b> (Click to expand)</summary>
<br>

To allow the script to perform actions on your behalf, you need a Personal Access Token:
1. Log in to your GitHub account.
2. Click your profile photo in the top-right corner, then click **Settings**.
3. In the left sidebar, scroll down and click **Developer settings**.
4. In the left sidebar, click **Personal access tokens**, then click **Tokens (classic)**.
5. Click the **Generate new token** button, and select **Generate new token (classic)**.
6. In the "Note" field, give your token a descriptive name (e.g., "Achievement Farmer").
7. Set the "Expiration" to a short duration (e.g., 7 days) for security.
8. Under "Select scopes", check the boxes for:
   - ✅ **`repo`** (Full control of private repositories)
   - ✅ **`read:user`** (Read all user profile data)
9. Scroll to the bottom and click **Generate token**.
10. **Copy the generated token immediately!** You won't be able to see it again after leaving the page. You will paste this token into the terminal when the script runs.

</details>

---

## 🚀 Usage

1. Open your terminal and navigate to this folder.
2. Run the script:
   ```bash
   python achievements.py
   ```
3. Paste your **GitHub Token** when prompted.
4. Select which achievements you want to unlock (or press `A` to run them all).
5. Sit back and watch the script do the heavy lifting!

> **Note**: After you are done unlocking your achievements, it is highly recommended to delete your Personal Access Token from your GitHub Developer Settings for security purposes.

---

## ⚠️ Safety Notes
> 🛡️ **Please Read Before Running**
* GitHub API rate limits apply.
* Run responsibly to avoid abuse flags.
* Some achievements can require time and GitHub-side processing delay.
* Use only on accounts and repositories you control.
* Revoke and rotate tokens regularly.

---

<div align="center">

## 👤 Author

[![Telegram](https://img.shields.io/badge/Telegram-w4simg-blue?style=for-the-badge&logo=telegram)](https://t.me/w4simg)
[![Instagram](https://img.shields.io/badge/Instagram-w4simg-purple?style=for-the-badge&logo=instagram)](https://instagram.com/w4simg)
[![GitHub](https://img.shields.io/badge/GitHub-w4simg-black?style=for-the-badge&logo=github)](https://github.com/w4simg)

</div>
