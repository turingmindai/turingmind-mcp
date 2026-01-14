"""
Tree-sitter manager for loading and managing language grammars.

Similar to DevContext's TreeSitterManager, provides parsers for
JavaScript, TypeScript, and Python.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

try:
    import tree_sitter
    from tree_sitter import Language, Parser
except ImportError:
    tree_sitter = None
    Language = None
    Parser = None

logger = logging.getLogger("turingmind-mcp")


class TreeSitterManager:
    """Manages tree-sitter language grammars and parsers."""

    def __init__(self):
        """Initialize the manager."""
        self.loaded_grammars: Dict[str, Language] = {}
        self.initialized = False

    def initialize_grammars(self, languages: list[str]) -> bool:
        """
        Initialize tree-sitter language grammars.

        Args:
            languages: List of language names to load ('javascript', 'python', 'typescript', 'tsx')

        Returns:
            True if all languages were loaded successfully
        """
        if not tree_sitter:
            logger.error("tree-sitter not installed. Install with: pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript")
            return False

        if self.initialized:
            logger.warning("TreeSitterManager already initialized")
            return True

        logger.info(f"Initializing tree-sitter grammars for: {', '.join(languages)}")

        all_successful = True

        for language in languages:
            try:
                if language == "python":
                    self._load_python_grammar()
                elif language == "javascript":
                    self._load_javascript_grammar()
                elif language in ("typescript", "tsx"):
                    self._load_typescript_grammar(language)
                else:
                    logger.warning(f"No loader available for language: {language}")
                    all_successful = False
            except Exception as e:
                logger.error(f"Failed to load grammar for {language}: {e}")
                all_successful = False

        self.initialized = len(self.loaded_grammars) > 0

        if not self.initialized:
            logger.error("Failed to initialize any tree-sitter grammars")
            return False

        logger.info(f"Successfully initialized {len(self.loaded_grammars)} tree-sitter grammars")
        return all_successful

    def _load_python_grammar(self):
        """Load Python grammar."""
        try:
            from tree_sitter_python import language as python_language

            self.loaded_grammars["python"] = python_language
            logger.info("Successfully loaded grammar for python")
        except ImportError as e:
            logger.error(f"Failed to import tree-sitter-python: {e}")
            raise

    def _load_javascript_grammar(self):
        """Load JavaScript grammar."""
        try:
            from tree_sitter_javascript import language as js_language

            self.loaded_grammars["javascript"] = js_language
            logger.info("Successfully loaded grammar for javascript")
        except ImportError as e:
            logger.error(f"Failed to import tree-sitter-javascript: {e}")
            raise

    def _load_typescript_grammar(self, language: str):
        """Load TypeScript or TSX grammar."""
        try:
            from tree_sitter_typescript import language_ts, language_tsx

            if language == "typescript":
                self.loaded_grammars["typescript"] = language_ts
                logger.info("Successfully loaded grammar for typescript")
            elif language == "tsx":
                self.loaded_grammars["tsx"] = language_tsx
                logger.info("Successfully loaded grammar for tsx")
        except ImportError as e:
            logger.error(f"Failed to import tree-sitter-typescript: {e}")
            raise

    def get_parser_for_language(self, language_name: str) -> Optional[Parser]:
        """
        Get a parser for the specified language.

        Args:
            language_name: Name of the language ('javascript', 'python', 'typescript', 'tsx')

        Returns:
            Initialized parser for the language or None if not available
        """
        if not self.initialized:
            logger.warning("TreeSitterManager.get_parser_for_language called before initialization")
            return None

        grammar = self.loaded_grammars.get(language_name)

        if not grammar:
            logger.warning(f"No grammar loaded for language: {language_name}")
            return None

        try:
            parser = Parser()
            parser.set_language(grammar)
            return parser
        except Exception as e:
            logger.error(f"Failed to create parser for {language_name}: {e}")
            return None

    def has_language(self, language_name: str) -> bool:
        """Check if a language grammar is loaded."""
        return language_name in self.loaded_grammars

    def get_loaded_languages(self) -> list[str]:
        """Get list of loaded language grammars."""
        return list(self.loaded_grammars.keys())
