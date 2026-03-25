"""Shared context passed to tool handlers."""

from __future__ import annotations

import logging
from typing import Any, Callable


class ToolContext:
    """Context passed to each tool handler: client, api_url, headers, and server helpers."""

    __slots__ = (
        "client",
        "api_url",
        "headers",
        "logger",
        "save_api_key",
        "version",
        "get_db",
        "get_memory_manager",
        "get_repo_path",
        "get_config",
        "EntityIndexer",
        "get_chat_analysis_agent",
    )

    def __init__(
        self,
        *,
        client: Any,
        api_url: str,
        headers: dict[str, str],
        logger: logging.Logger,
        save_api_key: Callable[[str, str | None], str],
        version: str,
        get_db: Any = None,
        get_memory_manager: Any = None,
        get_repo_path: Any = None,
        get_config: Any = None,
        entity_indexer_cls: Any = None,
        get_chat_analysis_agent: Any = None,
    ):
        self.client = client
        self.api_url = api_url
        self.headers = headers
        self.logger = logger
        self.save_api_key = save_api_key
        self.version = version
        self.get_db = get_db
        self.get_memory_manager = get_memory_manager
        self.get_repo_path = get_repo_path
        self.get_config = get_config
        self.EntityIndexer = entity_indexer_cls
        self.get_chat_analysis_agent = get_chat_analysis_agent
