"""
Code entity indexing using AST parsing.

Extracts functions, classes, and files from codebase for relationship tracking.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("turingmind-mcp")


class EntityIndexer:
    """Indexes code entities from source files."""

    SUPPORTED_LANGUAGES = ["javascript", "typescript", "python"]
    SUPPORTED_EXTENSIONS = {
        "javascript": [".js", ".jsx", ".mjs"],
        "typescript": [".ts", ".tsx"],
        "python": [".py"],
    }

    def __init__(self, repo_path: str):
        """Initialize indexer with repository path."""
        self.repo_path = Path(repo_path)
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")

    def index_codebase(
        self,
        languages: Optional[List[str]] = None,
        force_reindex: bool = False,
    ) -> Dict[str, Any]:
        """Index entire codebase."""
        if languages is None:
            languages = self.SUPPORTED_LANGUAGES

        entities = []
        relationships = []

        # Find all code files
        for lang in languages:
            extensions = self.SUPPORTED_EXTENSIONS.get(lang, [])
            for ext in extensions:
                for file_path in self.repo_path.rglob(f"*{ext}"):
                    if self._should_skip_file(file_path):
                        continue

                    try:
                        file_entities, file_relationships = self._index_file(
                            str(file_path.relative_to(self.repo_path)), lang
                        )
                        entities.extend(file_entities)
                        relationships.extend(file_relationships)
                    except Exception as e:
                        logger.warning(f"Failed to index {file_path}: {e}")

        return {
            "indexed": len(entities),
            "entities_by_type": self._count_by_type(entities),
            "languages": languages,
            "relationships": len(relationships),
            "status": "completed",
            "entities": entities,
            "relationships": relationships,
        }

    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped."""
        # Skip node_modules, venv, .git, etc.
        skip_dirs = {"node_modules", "venv", ".git", "__pycache__", "dist", "build"}
        parts = file_path.parts
        return any(part in skip_dirs for part in parts)

    def _index_file(self, file_path: str, language: str) -> tuple[List[Dict], List[Dict]]:
        """Index a single file."""
        full_path = self.repo_path / file_path

        if language == "python":
            return self._index_python_file(file_path, full_path)
        elif language in ("javascript", "typescript"):
            return self._index_js_file(file_path, full_path, language)
        else:
            return [], []

    def _index_python_file(
        self, file_path: str, full_path: Path
    ) -> tuple[List[Dict], List[Dict]]:
        """Index Python file using AST."""
        entities = []
        relationships = []

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
                tree = ast.parse(content, filename=str(full_path))

            # Add file entity
            file_entity_id = f"{file_path}:file"
            entities.append({
                "entity_id": file_entity_id,
                "file_path": file_path,
                "entity_type": "file",
                "name": Path(file_path).name,
                "start_line": 1,
                "end_line": len(content.splitlines()),
                "language": "python",
            })

            # Extract functions and classes
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    entity_id = f"{file_path}:{node.name}:function"
                    entities.append({
                        "entity_id": entity_id,
                        "file_path": file_path,
                        "entity_type": "function",
                        "name": node.name,
                        "start_line": node.lineno,
                        "end_line": node.end_lineno or node.lineno,
                        "language": "python",
                    })

                    # Extract calls (simplified)
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                target_name = child.func.id
                                relationships.append({
                                    "source_entity_id": entity_id,
                                    "target_symbol_name": target_name,
                                    "relationship_type": "calls",
                                })

                elif isinstance(node, ast.ClassDef):
                    entity_id = f"{file_path}:{node.name}:class"
                    entities.append({
                        "entity_id": entity_id,
                        "file_path": file_path,
                        "entity_type": "class",
                        "name": node.name,
                        "start_line": node.lineno,
                        "end_line": node.end_lineno or node.lineno,
                        "language": "python",
                    })

                    # Extract method calls
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            method_id = f"{file_path}:{node.name}.{item.name}:method"
                            entities.append({
                                "entity_id": method_id,
                                "file_path": file_path,
                                "entity_type": "function",
                                "name": f"{node.name}.{item.name}",
                                "start_line": item.lineno,
                                "end_line": item.end_lineno or item.lineno,
                                "language": "python",
                            })

        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
        except Exception as e:
            logger.error(f"Error indexing {file_path}: {e}")

        return entities, relationships

    def _index_js_file(
        self, file_path: str, full_path: Path, language: str
    ) -> tuple[List[Dict], List[Dict]]:
        """Index JavaScript/TypeScript file (simplified - would need proper parser)."""
        entities = []
        relationships = []

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
                lines = content.splitlines()

            # Add file entity
            file_entity_id = f"{file_path}:file"
            entities.append({
                "entity_id": file_entity_id,
                "file_path": file_path,
                "entity_type": "file",
                "name": Path(file_path).name,
                "start_line": 1,
                "end_line": len(lines),
                "language": language,
            })

            # Simple regex-based extraction (simplified)
            # Real implementation would use proper JS/TS parser
            import re

            # Extract function declarations
            func_pattern = r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
            for match in re.finditer(func_pattern, content):
                func_name = match.group(1)
                line_num = content[: match.start()].count("\n") + 1
                entity_id = f"{file_path}:{func_name}:function"
                entities.append({
                    "entity_id": entity_id,
                    "file_path": file_path,
                    "entity_type": "function",
                    "name": func_name,
                    "start_line": line_num,
                    "end_line": line_num + 10,  # Approximate
                    "language": language,
                })

            # Extract class declarations
            class_pattern = r"(?:export\s+)?class\s+(\w+)"
            for match in re.finditer(class_pattern, content):
                class_name = match.group(1)
                line_num = content[: match.start()].count("\n") + 1
                entity_id = f"{file_path}:{class_name}:class"
                entities.append({
                    "entity_id": entity_id,
                    "file_path": file_path,
                    "entity_type": "class",
                    "name": class_name,
                    "start_line": line_num,
                    "end_line": line_num + 50,  # Approximate
                    "language": language,
                })

            # Extract imports
            import_pattern = r"import\s+(?:\{[^}]+\}|\*\s+as\s+\w+|\w+)\s+from\s+['\"]([^'\"]+)['\"]"
            for match in re.finditer(import_pattern, content):
                import_path = match.group(1)
                relationships.append({
                    "source_entity_id": file_entity_id,
                    "target_symbol_name": import_path,
                    "relationship_type": "imports",
                })

        except Exception as e:
            logger.error(f"Error indexing {file_path}: {e}")

        return entities, relationships

    def _count_by_type(self, entities: List[Dict]) -> Dict[str, int]:
        """Count entities by type."""
        counts = {}
        for entity in entities:
            entity_type = entity.get("entity_type", "unknown")
            counts[entity_type] = counts.get(entity_type, 0) + 1
        return counts


def get_repo_path() -> Optional[str]:
    """Get repository path from current directory or git."""
    import subprocess

    try:
        # Try to get git root
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback to current directory
        return os.getcwd()
