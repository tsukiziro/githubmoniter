import os
import random
import base64
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, Dict, Any, List
import httpx

logger = logging.getLogger(__name__)

CO_AUTHOR = "w4simg-assistant <w4simg-assistant@users.noreply.github.com>"
DEFAULT_REPO_NAME = "free-achievements"

class AchievementService:
    """
    Asynchronous GitHub Achievements Unlocker.
    Uses user's existing OAuth/PAT token.
    Injects humanized delays and randomized jitter between every single request
    to guarantee account safety and prevent GitHub bot/abuse flags.
    """
    BASE_URL = "https://api.github.com"
    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, token: str, username: str, progress_callback: Optional[Callable[[int, str], Awaitable[None]]] = None):
        self.token = token
        self.username = username
        self.repo = DEFAULT_REPO_NAME
        self.progress_callback = progress_callback
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Mozilla/5.0 (GitHub-Guardian-Agent)"
        }
        self.graphql_headers = {
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (GitHub-Guardian-Agent)"
        }

    async def _safe_delay(self, min_s: float = 2.0, max_s: float = 4.0, action_desc: str = ""):
        """Injects humanized delay with randomized jitter across all actions."""
        delay = round(random.uniform(min_s, max_s), 2)
        if action_desc and self.progress_callback:
            logger.debug(f"Pacing action '{action_desc}': sleeping {delay}s...")
        await asyncio.sleep(delay)

    async def _notify_progress(self, percent: int, text: str):
        if self.progress_callback:
            try:
                await self.progress_callback(percent, text)
            except Exception as e:
                logger.debug(f"Progress callback error: {e}")

    async def _request(self, client: httpx.AsyncClient, method: str, endpoint: str, json_data: Any = None, params: Any = None, extra_headers: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self.BASE_URL}{endpoint}" if not endpoint.startswith("http") else endpoint
        req_headers = {**self.headers, **(extra_headers or {})}
        
        for attempt in range(4):
            try:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    json=json_data,
                    params=params,
                    timeout=20.0
                )
                if resp.status_code == 204:
                    return True
                if resp.status_code in (200, 201):
                    return resp.json() if resp.content else True
                if resp.status_code == 404:
                    return None
                if resp.status_code == 409: # Conflict / already enabled
                    return resp.json() if resp.content else True
                
                # Rate limit handling
                if resp.status_code in (403, 429):
                    logger.warning(f"Rate limited or forbidden ({resp.status_code}), backing off...")
                    await asyncio.sleep(6)
                    continue

                if resp.status_code >= 400:
                    err_msg = resp.text[:250]
                    logger.warning(f"GitHub API {method} {endpoint} returned {resp.status_code}: {err_msg}")
                    return None
            except (httpx.RequestError, httpx.TimeoutException) as ex:
                logger.warning(f"Network error on attempt {attempt+1}/4: {ex}")
                await asyncio.sleep(3)
        return None

    async def _graphql(self, client: httpx.AsyncClient, query: str, variables: Optional[Dict[str, Any]] = None) -> Any:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = await client.post(self.GRAPHQL_URL, headers=self.graphql_headers, json=payload, timeout=20.0)
            if resp.status_code == 200:
                data = resp.json()
                if "errors" in data:
                    logger.warning(f"GraphQL Errors: {data['errors']}")
                    return None
                return data.get("data")
        except Exception as e:
            logger.warning(f"GraphQL exception: {e}")
        return None

    # --- Setup Dedicated Repository ---
    async def ensure_repository_ready(self, client: httpx.AsyncClient) -> bool:
        """Verifies or creates dedicated public repository 'free-achievements'."""
        await self._notify_progress(5, "🔍 Checking dedicated achievement repository...")
        repo_data = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}")
        
        if not repo_data:
            await self._safe_delay(1.5, 2.5, "repo creation delay")
            await self._notify_progress(8, f"📦 Creating public repository <code>{self.repo}</code>...")
            create_payload = {
                "name": self.repo,
                "private": False, # Public required for Pages & Public Sighting
                "auto_init": True,
                "description": "Dedicated repository for unlocking GitHub achievements."
            }
            repo_data = await self._request(client, "POST", "/user/repos", json_data=create_payload)
            if not repo_data:
                logger.error("Failed to create dedicated achievements repository.")
                return False
            await self._safe_delay(2.5, 3.5, "wait for repo indexing")

        # Check if default branch and initial commit exist
        await self._safe_delay(1.5, 2.5)
        branch_info = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/git/refs/heads/main")
        if not branch_info:
            branch_info = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/git/refs/heads/master")
            
        if not branch_info:
            # Commit initial README
            await self._notify_progress(10, "📄 Initializing repository root README...")
            content_b64 = base64.b64encode(b"# Free GitHub Achievements\nDedicated repository for achievement farming.\n").decode()
            await self._request(client, "PUT", f"/repos/{self.username}/{self.repo}/contents/README.md", json_data={
                "message": "Initial commit",
                "content": content_b64,
                "branch": "main"
            })
            await self._safe_delay(2.0, 3.0)
            
        return True

    async def _get_default_branch(self, client: httpx.AsyncClient) -> tuple[str, str]:
        repo_data = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}")
        branch = repo_data.get("default_branch", "main") if repo_data else "main"
        
        ref_data = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/git/refs/heads/{branch}")
        sha = ref_data.get("object", {}).get("sha", "") if ref_data else ""
        return branch, sha

    async def _create_branch(self, client: httpx.AsyncClient, branch_name: str, base_sha: str) -> bool:
        res = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/git/refs", json_data={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        })
        return bool(res)

    async def _commit_file(self, client: httpx.AsyncClient, path: str, message: str, content: str, branch: str, file_sha: Optional[str] = None) -> bool:
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": branch
        }
        if file_sha:
            payload["sha"] = file_sha
        res = await self._request(client, "PUT", f"/repos/{self.username}/{self.repo}/contents/{path.lstrip('/')}", json_data=payload)
        return bool(res)

    async def _create_and_merge_pr(self, client: httpx.AsyncClient, label: str, base_branch: str, base_sha: str, commit_msg: str) -> bool:
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        feature_branch = f"auto/{label.lower().replace(' ', '-')}-{ts[:12]}"
        
        # 1. Create feature branch
        if not await self._create_branch(client, feature_branch, base_sha):
            return False
        await self._safe_delay(2.0, 3.5, "branch to commit jitter")

        # 2. Commit file
        file_path = f"notes/update-{ts[:8]}.txt"
        file_content = f"Automated achievement update at {datetime.now(timezone.utc).isoformat()}\n"
        if not await self._commit_file(client, file_path, commit_msg, file_content, feature_branch):
            return False
        await self._safe_delay(2.0, 3.5, "commit to PR jitter")

        # 3. Create PR
        pr_data = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/pulls", json_data={
            "title": f"[Auto] {label} Achievement",
            "body": f"Automated pull request to unlock {label} badge.",
            "head": feature_branch,
            "base": base_branch
        })
        if not pr_data or "number" not in pr_data:
            return False
            
        pr_number = pr_data["number"]
        await self._safe_delay(2.5, 4.0, "PR to merge human delay")

        # 4. Merge PR
        merge_res = await self._request(client, "PUT", f"/repos/{self.username}/{self.repo}/pulls/{pr_number}/merge", json_data={
            "merge_method": "merge"
        })
        await self._safe_delay(2.0, 3.0, "post-merge cooldown")
        return bool(merge_res)

    # ══════════════════════════════════════════════════════════════════════════
    #  ACHIEVEMENT UNLOCK METHODS (ALL WITH HUMANIZED DELAYS & COOLDOWNS)
    # ══════════════════════════════════════════════════════════════════════════

    async def unlock_quickdraw(self, client: httpx.AsyncClient) -> bool:
        """🚀 Quickdraw: Open an issue and close it immediately (within 5 minutes)."""
        await self._notify_progress(20, "🚀 Unlocking <b>Quickdraw</b>: Creating issue...")
        issue = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/issues", json_data={
            "title": "⚡ Quickdraw verification ticket",
            "body": "Automated verification ticket for Quickdraw achievement."
        })
        if not issue or "number" not in issue:
            return False
            
        num = issue["number"]
        # Human delay before closing (safely within 5 min limit)
        await self._safe_delay(2.5, 4.0, "quickdraw close delay")
        await self._notify_progress(35, f"🚀 Unlocking <b>Quickdraw</b>: Closing issue #{num}...")
        
        closed = await self._request(client, "PATCH", f"/repos/{self.username}/{self.repo}/issues/{num}", json_data={
            "state": "closed"
        })
        await self._safe_delay(2.0, 3.0)
        return bool(closed)

    async def unlock_yolo(self, client: httpx.AsyncClient) -> bool:
        """🤠 YOLO: Merge a Pull Request without requesting any code review."""
        await self._notify_progress(30, "🤠 Unlocking <b>YOLO</b>: Branching & pushing commit...")
        branch, sha = await self._get_default_branch(client)
        if not sha:
            return False
            
        ok = await self._create_and_merge_pr(client, "YOLO", branch, sha, "chore: automated YOLO merge commit")
        return ok

    async def unlock_pair_extraordinaire(self, client: httpx.AsyncClient) -> bool:
        """👥 Pair Extraordinaire: PR with a Co-authored-by commit."""
        await self._notify_progress(45, "👥 Unlocking <b>Pair Extraordinaire</b>: Co-authoring commit...")
        branch, sha = await self._get_default_branch(client)
        if not sha:
            return False
            
        commit_msg = f"chore: co-authored update\n\nCo-authored-by: {CO_AUTHOR}"
        ok = await self._create_and_merge_pr(client, "Pair-Extraordinaire", branch, sha, commit_msg)
        return ok

    async def unlock_galaxy_brain(self, client: httpx.AsyncClient) -> bool:
        """🧠 Galaxy Brain: Create Q&A discussion, post answer, mark accepted."""
        await self._notify_progress(55, "🧠 Unlocking <b>Galaxy Brain</b>: Activating repository discussions...")
        # 1. Enable discussions
        await self._request(client, "PATCH", f"/repos/{self.username}/{self.repo}", json_data={"has_discussions": True})
        await self._safe_delay(2.5, 4.0, "enable discussions wait")

        # 2. GraphQL: Fetch categories
        query_categories = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id
            discussionCategories(first: 10) {
              nodes { id name isAnswerable }
            }
          }
        }
        """
        data = await self._graphql(client, query_categories, {"owner": self.username, "name": self.repo})
        if not data or "repository" not in data:
            logger.warning("Could not fetch discussion categories via GraphQL.")
            return False
            
        repo_id = data["repository"]["id"]
        categories = data["repository"].get("discussionCategories", {}).get("nodes", [])
        qa_cat = next((c for c in categories if c.get("isAnswerable")), None)
        if not qa_cat:
            qa_cat = categories[0] if categories else None
        if not qa_cat:
            return False

        await self._safe_delay(2.0, 3.5)
        await self._notify_progress(65, "🧠 Unlocking <b>Galaxy Brain</b>: Creating Q&A discussion thread...")

        # 3. Create Discussion
        create_mutation = """
        mutation($repoId: ID!, $catId: ID!, $title: String!, $body: String!) {
          createDiscussion(input: {
            repositoryId: $repoId, categoryId: $catId,
            title: $title, body: $body
          }) { discussion { id url } }
        }
        """
        disc_res = await self._graphql(client, create_mutation, {
            "repoId": repo_id,
            "catId": qa_cat["id"],
            "title": "How to optimize GitHub developer productivity?",
            "body": "Share the top automated practices for software contribution and developer profile activity."
        })
        if not disc_res or "createDiscussion" not in disc_res:
            return False
            
        disc_id = disc_res["createDiscussion"]["discussion"]["id"]
        await self._safe_delay(3.0, 4.5, "wait before posting answer")
        await self._notify_progress(72, "🧠 Unlocking <b>Galaxy Brain</b>: Submitting detailed answer...")

        # 4. Add Answer Comment
        comment_mutation = """
        mutation($discId: ID!, $body: String!) {
          addDiscussionComment(input: { discussionId: $discId, body: $body }) {
            comment { id }
          }
        }
        """
        ans_res = await self._graphql(client, comment_mutation, {
            "discId": disc_id,
            "body": "Establish continuous integration, automate repetitive commits with verified schedules, and keep repositories structured with clean documentation."
        })
        if not ans_res or "addDiscussionComment" not in ans_res:
            return False
            
        comment_id = ans_res["addDiscussionComment"]["comment"]["id"]
        await self._safe_delay(2.5, 4.0, "wait before accepting answer")
        await self._notify_progress(78, "🧠 Unlocking <b>Galaxy Brain</b>: Marking answer as accepted...")

        # 5. Mark Answer Accepted
        mark_mutation = """
        mutation($commentId: ID!) {
          markDiscussionCommentAsAnswer(input: { id: $commentId }) {
            discussion { id }
          }
        }
        """
        mark_res = await self._graphql(client, mark_mutation, {"commentId": comment_id})
        await self._safe_delay(2.0, 3.0)
        return bool(mark_res)

    async def unlock_pull_shark(self, client: httpx.AsyncClient, count: int = 2) -> bool:
        """🦈 Pull Shark (Bronze/Silver): Merges PRs with anti-bot randomized pacing."""
        branch, sha = await self._get_default_branch(client)
        if not sha:
            return False
            
        for i in range(1, count + 1):
            await self._notify_progress(
                int(20 + (i / count) * 60),
                f"🦈 <b>Pull Shark</b>: Merging PR {i}/{count} (Safe pacing enabled)..."
            )
            success = await self._create_and_merge_pr(client, f"Pull-Shark-{i}", branch, sha, f"chore: pull shark PR #{i}")
            if not success:
                logger.warning(f"Pull Shark PR #{i} could not be merged.")
            # Significant anti-flagging delay between each Pull Shark cycle
            await self._safe_delay(3.0, 5.0, f"Pull Shark PR #{i} cooldown")
            branch, sha = await self._get_default_branch(client)
            
        return True

    async def unlock_heart_on_sleeve(self, client: httpx.AsyncClient) -> bool:
        """❤️ Heart On Sleeve: Give ❤️ reactions to 10+ issues or PRs."""
        await self._notify_progress(25, "❤️ Unlocking <b>Heart on Sleeve</b>: Gathering issues...")
        issues = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/issues", params={"state": "all", "per_page": 15})
        existing_nums = [i["number"] for i in (issues or []) if isinstance(i, dict)]

        # Create needed issues if fewer than 10
        needed = max(0, 10 - len(existing_nums))
        for i in range(needed):
            await self._safe_delay(2.0, 3.5, f"create target issue {i+1}")
            await self._notify_progress(25 + int((i / 10) * 20), f"❤️ Creating target issue {i+1}/{needed}...")
            new_issue = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/issues", json_data={
                "title": f"Community feedback target #{i+1}",
                "body": "Target thread for developer reactions and feedback."
            })
            if new_issue and "number" in new_issue:
                existing_nums.append(new_issue["number"])

        # React with ❤️ on 10 issues with safe delay
        react_headers = {"Accept": "application/vnd.github.squirrel-girl-preview+json"}
        reacted_count = 0
        for idx, num in enumerate(existing_nums[:10], start=1):
            await self._notify_progress(50 + int((idx / 10) * 45), f"❤️ Giving heart reaction {idx}/10 (Anti-bot pacing)...")
            res = await self._request(
                client, "POST",
                f"/repos/{self.username}/{self.repo}/issues/{num}/reactions",
                json_data={"content": "heart"},
                extra_headers=react_headers
            )
            if res:
                reacted_count += 1
            await self._safe_delay(2.0, 3.5, f"reaction {idx} cooldown")
            
        return reacted_count >= 10

    async def unlock_public_sighting(self, client: httpx.AsyncClient) -> bool:
        """👀 Public Sighting: Mention @username in a public repository issue."""
        await self._notify_progress(40, f"👀 Unlocking <b>Public Sighting</b>: Mentioning @{self.username}...")
        await self._safe_delay(2.0, 3.5)
        res = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/issues", json_data={
            "title": f"Public mention shoutout to maintainer @{self.username}",
            "body": f"Big kudos to @{self.username} for building and maintaining projects in the open source ecosystem! 🚀"
        })
        await self._safe_delay(2.0, 3.0)
        return bool(res)

    async def unlock_first_interaction(self, client: httpx.AsyncClient) -> bool:
        """💬 First Interaction: Post first comment on any issue."""
        await self._notify_progress(45, "💬 Unlocking <b>First Interaction</b>: Finding issue to comment...")
        issues = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/issues", params={"state": "all", "per_page": 5})
        target_num = 1
        if issues and isinstance(issues, list):
            target_num = issues[0].get("number", 1)

        await self._safe_delay(2.5, 4.0, "comment prep delay")
        await self._notify_progress(65, f"💬 Submitting comment on issue #{target_num}...")
        res = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/issues/{target_num}/comments", json_data={
            "body": "Excited to collaborate and share progress on this repository! Keep up the momentum. ✨"
        })
        await self._safe_delay(2.0, 3.0)
        return bool(res)

    async def unlock_open_source_heart(self, client: httpx.AsyncClient) -> bool:
        """🌱 Open Source Heart: Tag repository with hacktoberfest topic and merge PR."""
        await self._notify_progress(35, "🌱 Unlocking <b>Open Source Heart</b>: Tagging repository with hacktoberfest topic...")
        topic_headers = {"Accept": "application/vnd.github.mercy-preview+json"}
        await self._request(client, "PUT", f"/repos/{self.username}/{self.repo}/topics", json_data={
            "names": ["hacktoberfest", "open-source", "automation", "python"]
        }, extra_headers=topic_headers)
        await self._safe_delay(2.5, 4.0, "topic indexing delay")

        await self._notify_progress(60, "🌱 Merging contribution PR into hacktoberfest-tagged repo...")
        branch, sha = await self._get_default_branch(client)
        if not sha:
            return False
            
        ok = await self._create_and_merge_pr(client, "Hacktoberfest-Contribution", branch, sha, "chore: hacktoberfest community contribution")
        return ok

    async def unlock_github_pages(self, client: httpx.AsyncClient) -> bool:
        """🌐 GitHub Pages: Commit index.html and publish website via GitHub Pages API."""
        await self._notify_progress(40, "🌐 Unlocking <b>GitHub Pages</b>: Committing index.html...")
        branch, _ = await self._get_default_branch(client)
        
        html_code = (
            "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
            "  <meta charset='UTF-8'>\n"
            "  <title>Free GitHub Achievements</title>\n"
            "</head>\n<body style='font-family:sans-serif; text-align:center; padding:50px;'>\n"
            "  <h1>🚀 GitHub Pages Achievement Unlocked</h1>\n"
            "  <p>Automated showcase deployed via GitHub Guardian.</p>\n"
            "</body>\n</html>\n"
        )
        existing = await self._request(client, "GET", f"/repos/{self.username}/{self.repo}/contents/index.html", params={"ref": branch})
        file_sha = existing.get("sha") if existing else None

        await self._commit_file(client, "index.html", "chore: add GitHub Pages index.html", html_code, branch, file_sha)
        await self._safe_delay(2.5, 4.0, "wait before enabling pages")

        await self._notify_progress(70, "🌐 Enabling GitHub Pages via API...")
        pages_headers = {"Accept": "application/vnd.github+json"}
        res = await self._request(client, "POST", f"/repos/{self.username}/{self.repo}/pages", json_data={
            "source": {"branch": branch, "path": "/"}
        }, extra_headers=pages_headers)
        await self._safe_delay(2.0, 3.0)
        return bool(res)

    # ══════════════════════════════════════════════════════════════════════════
    #  MASTER RUNNERS
    # ══════════════════════════════════════════════════════════════════════════

    async def run_single(self, achievement_key: str) -> bool:
        """Runs an individual achievement with guaranteed pacing and status reporting."""
        async with httpx.AsyncClient() as client:
            if not await self.ensure_repository_ready(client):
                return False

            dispatch = {
                "quickdraw": self.unlock_quickdraw,
                "yolo": self.unlock_yolo,
                "pair": self.unlock_pair_extraordinaire,
                "galaxy_brain": self.unlock_galaxy_brain,
                "pull_shark": lambda c: self.unlock_pull_shark(c, count=2),
                "heart": self.unlock_heart_on_sleeve,
                "public_sighting": self.unlock_public_sighting,
                "first_interaction": self.unlock_first_interaction,
                "open_source_heart": self.unlock_open_source_heart,
                "github_pages": self.unlock_github_pages
            }

            handler = dispatch.get(achievement_key)
            if not handler:
                logger.error(f"Unknown achievement key: {achievement_key}")
                return False

            result = await handler(client)
            await self._notify_progress(100, f"✅ Processing finished! Result: {'SUCCESS' if result else 'COMPLETED'}")
            return result

    async def run_all(self) -> Dict[str, bool]:
        """Runs all achievements sequentially with safe pacing between each badge."""
        results = {}
        async with httpx.AsyncClient() as client:
            if not await self.ensure_repository_ready(client):
                return {"repo_init": False}

            achievements = [
                ("quickdraw", "🚀 Quickdraw", self.unlock_quickdraw),
                ("yolo", "🤠 YOLO", self.unlock_yolo),
                ("pair", "👥 Pair Extraordinaire", self.unlock_pair_extraordinaire),
                ("first_interaction", "💬 First Interaction", self.unlock_first_interaction),
                ("public_sighting", "👀 Public Sighting", self.unlock_public_sighting),
                ("heart", "❤️ Heart on Sleeve", self.unlock_heart_on_sleeve),
                ("open_source_heart", "🌱 Open Source Heart", self.unlock_open_source_heart),
                ("github_pages", "🌐 GitHub Pages", self.unlock_github_pages),
                ("galaxy_brain", "🧠 Galaxy Brain", self.unlock_galaxy_brain),
                ("pull_shark", "🦈 Pull Shark", lambda c: self.unlock_pull_shark(c, count=2)),
            ]

            total = len(achievements)
            for idx, (key, title, handler) in enumerate(achievements, start=1):
                base_pct = int(((idx - 1) / total) * 100)
                await self._notify_progress(base_pct, f"[{idx}/{total}] Processing <b>{title}</b>...")
                try:
                    res = await handler(client)
                    results[key] = bool(res)
                except Exception as ex:
                    logger.error(f"Error unlocking {title}: {ex}", exc_info=True)
                    results[key] = False

                # Humanized cooldown between badges (3.0s - 5.0s)
                if idx < total:
                    await self._safe_delay(3.0, 5.0, f"cooldown after {title}")

            await self._notify_progress(100, "🎉 All selected achievements have been processed successfully!")
            return results
