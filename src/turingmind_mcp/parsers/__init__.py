"""
Parser modules for code entity extraction using tree-sitter.

Provides language-specific parsers for Python, JavaScript, and TypeScript.
"""

from .tree_sitter_manager import TreeSitterManager
from .python_parser import parse_python
from .javascript_parser import parse_javascript
from .typescript_parser import parse_typescript

__all__ = [
    "TreeSitterManager",
    "parse_python",
    "parse_javascript",
    "parse_typescript",
]
