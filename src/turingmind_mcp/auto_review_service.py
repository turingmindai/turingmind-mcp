"""
Auto-review polling service for monitoring git commits.

Monitors repositories for new commits and triggers automatic code reviews.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("turingmind-mcp")


class AutoReviewService:
    """Service for monitoring repositories and triggering automatic reviews."""

    def __init__(self, db, memory_manager, api_url: str, api_key: Optional[str] = None):
        """Initialize auto-review service."""
        self.db = db
        self.memory_manager = memory_manager
        self.api_url = api_url
        self.api_key = api_key
        self.monitoring_tasks: Dict[str, asyncio.Task] = {}
        self.running = False

    async def start_monitoring(
        self,
        repo: str,
        branch: str = "main",
        review_type: str = "quick",
        poll_interval: int = 60,
    ) -> bool:
        """
        Start monitoring a repository for new commits.

        Args:
            repo: Repository identifier (owner/repo)
            branch: Branch to monitor
            review_type: Type of review to perform (quick/deep)
            poll_interval: Polling interval in seconds

        Returns:
            True if monitoring started successfully
        """
        if repo in self.monitoring_tasks:
            logger.warning(f"Already monitoring {repo}")
            return False

        task = asyncio.create_task(
            self._monitor_repo(repo, branch, review_type, poll_interval)
        )
        self.monitoring_tasks[repo] = task
        logger.info(f"Started monitoring {repo} on branch {branch}")
        return True

    async def stop_monitoring(self, repo: str) -> bool:
        """Stop monitoring a repository."""
        if repo not in self.monitoring_tasks:
            return False

        task = self.monitoring_tasks[repo]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        del self.monitoring_tasks[repo]
        logger.info(f"Stopped monitoring {repo}")
        return True

    async def _monitor_repo(
        self,
        repo: str,
        branch: str,
        review_type: str,
        poll_interval: int,
    ):
        """Monitor repository for new commits."""
        repo_path = self._get_repo_path()
        if not repo_path:
            logger.error(f"Could not determine repo path for {repo}")
            return

        last_commit = None

        while True:
            try:
                # Get latest commit on branch
                current_commit = self._get_latest_commit(repo_path, branch)
                
                if current_commit and current_commit != last_commit:
                    if last_commit:
                        # New commit detected, trigger review
                        logger.info(f"New commit detected in {repo}: {current_commit[:8]}")
                        await self._trigger_review(
                            repo, branch, current_commit, last_commit, review_type
                        )
                    
                    last_commit = current_commit

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info(f"Monitoring cancelled for {repo}")
                break
            except Exception as e:
                logger.error(f"Error monitoring {repo}: {e}")
                await asyncio.sleep(poll_interval)

    def _get_repo_path(self) -> Optional[Path]:
        """Get repository path from current directory or git config."""
        try:
            # Try to get repo path from git
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except Exception as e:
            logger.debug(f"Could not get git repo path: {e}")

        return None

    def _fetch_remote(self, repo_path: Path, branch: str) -> bool:
        """Fetch latest changes from remote for a branch."""
        try:
            result = subprocess.run(
                ["git", "fetch", "origin", branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,  # Fetch can take longer
            )
            if result.returncode != 0:
                logger.warning(f"Git fetch failed: {result.stderr}")
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Git fetch timed out for {repo_path}")
            return False
        except Exception as e:
            logger.debug(f"Could not fetch remote: {e}")
            return False

    def _get_latest_commit(self, repo_path: Path, branch: str) -> Optional[str]:
        """Get latest commit SHA for a branch after fetching from remote."""
        # Fetch first to ensure we have latest remote state
        self._fetch_remote(repo_path, branch)
        
        try:
            result = subprocess.run(
                ["git", "rev-parse", f"origin/{branch}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Could not get latest commit: {e}")

        return None

    async def _trigger_review(
        self,
        repo: str,
        branch: str,
        commit_sha: str,
        previous_sha: str,
        review_type: str,
    ):
        """Trigger a code review for new commits."""
        try:
            # Get changed files
            repo_path = self._get_repo_path()
            if not repo_path:
                return

            changed_files = self._get_changed_files(repo_path, previous_sha, commit_sha)
            
            if not changed_files:
                logger.info(f"No files changed in {repo}")
                return

            # Get commit message
            commit_message = self._get_commit_message(repo_path, commit_sha)

            # Get memory context
            memory_context = self.memory_manager.get_relevant_memory(repo, changed_files)

            # Trigger review via API (if available)
            if self.api_key:
                await self._upload_review_to_cloud(
                    repo, branch, commit_sha, changed_files, review_type, memory_context
                )
            else:
                logger.info(f"Would trigger {review_type} review for {repo}@{commit_sha}")
                logger.info(f"Changed files: {', '.join(changed_files[:5])}")

        except Exception as e:
            logger.error(f"Error triggering review: {e}")

    def _get_changed_files(
        self, repo_path: Path, from_sha: str, to_sha: str
    ) -> List[str]:
        """Get list of changed files between two commits."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", from_sha, to_sha],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.splitlines() if f.strip()]
        except Exception as e:
            logger.error(f"Error getting changed files: {e}")

        return []

    def _get_commit_message(self, repo_path: Path, commit_sha: str) -> str:
        """Get commit message for a commit."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%B", commit_sha],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Error getting commit message: {e}")

        return ""

    async def _upload_review_to_cloud(
        self,
        repo: str,
        branch: str,
        commit_sha: str,
        changed_files: List[str],
        review_type: str,
        memory_context: List[Dict[str, Any]],
    ):
        """Upload review trigger to cloud API."""
        # This would integrate with the cloud API to trigger reviews
        # For now, just log
        logger.info(
            f"Would upload review trigger: {repo}@{branch}#{commit_sha[:8]} "
            f"({len(changed_files)} files, {len(memory_context)} memory entries)"
        )


# Global service instance
_service_instance: Optional[AutoReviewService] = None


def get_auto_review_service(
    db=None, memory_manager=None, api_url: str = "", api_key: Optional[str] = None
) -> AutoReviewService:
    """Get or create auto-review service instance."""
    global _service_instance
    if _service_instance is None:
        if db is None or memory_manager is None:
            raise ValueError("db and memory_manager required for first initialization")
        _service_instance = AutoReviewService(db, memory_manager, api_url, api_key)
    return _service_instance
