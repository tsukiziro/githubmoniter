import base64
import logging
from typing import Optional, Dict, Any, List, Union
import httpx

logger = logging.getLogger(__name__)

class GitHubAPIException(Exception):
    """Custom exception for GitHub API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data

class GitHubAPIClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Guardian-Bot"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None
    ) -> Any:
        url = f"{self.BASE_URL}{endpoint}" if not endpoint.startswith("http") else endpoint
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    params=params,
                    json=json_data,
                    timeout=15.0
                )
                
                if resp.status_code == 204:
                    return True
                
                if resp.status_code >= 400:
                    error_msg = f"GitHub API error: {resp.status_code}"
                    try:
                        err_json = resp.json()
                        if "message" in err_json:
                            error_msg = f"GitHub Error ({resp.status_code}): {err_json['message']}"
                    except Exception:
                        err_json = None

                    if resp.status_code == 401:
                        raise GitHubAPIException("Authentication failed. Token may be revoked or expired.", 401, err_json)
                    elif resp.status_code == 403:
                        rate_remaining = resp.headers.get("x-ratelimit-remaining")
                        if rate_remaining and int(rate_remaining) == 0:
                            raise GitHubAPIException("GitHub API rate limit exceeded. Please try again later.", 403, err_json)
                        raise GitHubAPIException(f"Forbidden: {error_msg}. Insufficient permissions.", 403, err_json)
                    elif resp.status_code == 404:
                        raise GitHubAPIException("Resource or repository not found.", 404, err_json)
                    elif resp.status_code == 422:
                        raise GitHubAPIException(f"Unprocessable request: {error_msg}", 422, err_json)
                    else:
                        raise GitHubAPIException(error_msg, resp.status_code, err_json)

                return resp.json()
            except httpx.RequestError as e:
                logger.error(f"HTTP request error: {e}")
                raise GitHubAPIException("Network error while connecting to GitHub.") from e

    # --- User Profile ---
    async def get_user_profile(self) -> Dict[str, Any]:
        return await self._request("GET", "/user")

    # --- Repositories ---
    async def list_repositories(self, page: int = 1, per_page: int = 10, sort: str = "updated") -> List[Dict[str, Any]]:
        return await self._request("GET", "/user/repos", params={
            "page": page,
            "per_page": per_page,
            "sort": sort,
            "affiliation": "owner,collaborator"
        })

    async def get_repository(self, owner: str, repo: str) -> Dict[str, Any]:
        return await self._request("GET", f"/repos/{owner}/{repo}")

    async def create_repository(self, name: str, description: str = "", private: bool = False) -> Dict[str, Any]:
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": True
        }
        return await self._request("POST", "/user/repos", json_data=payload)

    async def delete_repository(self, owner: str, repo: str) -> bool:
        return await self._request("DELETE", f"/repos/{owner}/{repo}")

    # --- Files & Commits ---
    async def get_file_contents(self, owner: str, repo: str, path: str, ref: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if ref:
            params["ref"] = ref
        return await self._request("GET", f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", params=params)

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content_bytes: bytes,
        commit_message: str,
        branch: Optional[str] = None,
        sha: Optional[str] = None
    ) -> Dict[str, Any]:
        encoded_content = base64.b64encode(content_bytes).decode("utf-8")
        payload = {
            "message": commit_message,
            "content": encoded_content,
        }
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha

        return await self._request("PUT", f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", json_data=payload)

    # --- Issues ---
    async def list_issues(self, owner: str, repo: str, state: str = "open") -> List[Dict[str, Any]]:
        return await self._request("GET", f"/repos/{owner}/{repo}/issues", params={"state": state})

    async def create_issue(self, owner: str, repo: str, title: str, body: str = "") -> Dict[str, Any]:
        payload = {"title": title, "body": body}
        return await self._request("POST", f"/repos/{owner}/{repo}/issues", json_data=payload)

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")

    async def update_issue_state(self, owner: str, repo: str, issue_number: int, state: str) -> Dict[str, Any]:
        """state can be 'closed' or 'open'."""
        payload = {"state": state}
        return await self._request("PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json_data=payload)

    async def add_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> Dict[str, Any]:
        payload = {"body": body}
        return await self._request("POST", f"/repos/{owner}/{repo}/issues/{issue_number}/comments", json_data=payload)

    # --- Collaborators ---
    async def list_collaborators(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/repos/{owner}/{repo}/collaborators")

    async def add_collaborator(self, owner: str, repo: str, username: str, permission: str = "push") -> Dict[str, Any]:
        """permission: pull, push, admin, maintain, triage."""
        payload = {"permission": permission}
        return await self._request("PUT", f"/repos/{owner}/{repo}/collaborators/{username}", json_data=payload)

    async def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        return await self._request("DELETE", f"/repos/{owner}/{repo}/collaborators/{username}")

    # --- Activity & Analytics ---
    async def get_user_events(self, username: str) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/users/{username}/events", params={"per_page": 30})

    async def get_received_events(self, username: str) -> List[Dict[str, Any]]:
        """Events received by user (activity performed by collaborators, friends, and network)."""
        return await self._request("GET", f"/users/{username}/received_events", params={"per_page": 30})

    async def get_repo_commits(self, owner: str, repo: str, per_page: int = 20) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/repos/{owner}/{repo}/commits", params={"per_page": per_page})

    async def get_repo_views(self, owner: str, repo: str) -> Dict[str, Any]:
        """Requires push access to repository."""
        return await self._request("GET", f"/repos/{owner}/{repo}/traffic/views")

    async def get_repo_clones(self, owner: str, repo: str) -> Dict[str, Any]:
        """Requires push access to repository."""
        return await self._request("GET", f"/repos/{owner}/{repo}/traffic/clones")

    async def get_repo_stargazers(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/repos/{owner}/{repo}/stargazers")

    async def get_repo_forks(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/repos/{owner}/{repo}/forks")
