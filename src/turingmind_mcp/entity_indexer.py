"""
Code entity indexing using tree-sitter AST parsing.

Extracts functions, classes, and files from codebase for relationship tracking.
Similar to DevContext's indexing service.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .parsers import TreeSitterManager, parse_python, parse_javascript, parse_typescript

logger = logging.getLogger("turingmind-mcp")


class EntityIndexer:
    """Indexes code entities from source files using tree-sitter."""

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
        
        self.tree_sitter_manager = TreeSitterManager()
        self.parser_initialized = False

    async def initialize_parsers(self, languages: Optional[List[str]] = None):
        """Initialize tree-sitter parsers for specified languages."""
        if self.parser_initialized:
            return

        if languages is None:
            languages = self.SUPPORTED_LANGUAGES

        # Map language names to tree-sitter language identifiers
        ts_languages = []
        for lang in languages:
            if lang == "typescript":
                ts_languages.extend(["typescript", "tsx"])
            else:
                ts_languages.append(lang)

        success = self.tree_sitter_manager.initialize_grammars(ts_languages)
        if success:
            self.parser_initialized = True
            logger.info(f"Initialized parsers for: {', '.join(ts_languages)}")
        else:
            logger.warning("Some parsers failed to initialize, falling back to basic parsing")

    def index_codebase(
        self,
        languages: Optional[List[str]] = None,
        force_reindex: bool = False,
    ) -> Dict[str, Any]:
        """
        Index entire codebase.
        
        Args:
            languages: List of languages to index. Defaults to all supported.
            force_reindex: If True, caller should clear existing data before storing.
            
        Returns:
            Dictionary with:
            - indexed: Number of entities indexed
            - entities_by_type: Count by entity type
            - languages: Languages processed
            - relationships: Number of relationships
            - status: "completed" or "completed_with_errors"
            - entities: List of entity dictionaries
            - relationships: List of relationship dictionaries
            - failed_files: List of (file_path, error_message) tuples
        """
        if languages is None:
            languages = self.SUPPORTED_LANGUAGES

        # Initialize parsers synchronously (for now)
        # In async context, call initialize_parsers first
        if not self.parser_initialized:
            try:
                # Try to initialize synchronously
                ts_languages = []
                for lang in languages:
                    if lang == "typescript":
                        ts_languages.extend(["typescript", "tsx"])
                    else:
                        ts_languages.append(lang)
                self.tree_sitter_manager.initialize_grammars(ts_languages)
                self.parser_initialized = True
            except Exception as e:
                logger.warning(f"Failed to initialize tree-sitter parsers: {e}. Using fallback parsing.")

        entities = []
        relationships = []
        failed_files = []  # Track files that failed to index
        
        # Global entity registry for cross-file lookups
        # Maps (name, entity_type) -> list of entities for faster lookups
        entity_registry: Dict[tuple, List[Dict[str, Any]]] = {}
        
        # Collect all file paths first
        all_file_paths = []
        for lang in languages:
            extensions = self.SUPPORTED_EXTENSIONS.get(lang, [])
            for ext in extensions:
                for file_path in self.repo_path.rglob(f"*{ext}"):
                    if self._should_skip_file(file_path):
                        continue

                    relative_path = str(file_path.relative_to(self.repo_path))
                    
                    # Validate path to prevent traversal attacks
                    if not relative_path or ".." in relative_path or relative_path.startswith("/"):
                        logger.warning(f"Skipping suspicious path: {relative_path}")
                        continue
                    
                    all_file_paths.append((relative_path, lang))
        
        # First pass: Index all files and build entity registry
        for relative_path, lang in all_file_paths:
            try:
                file_entities, file_relationships = self._index_file(
                    relative_path, lang, entity_registry
                )
                entities.extend(file_entities)
                
                # Update registry with new entities
                for entity in file_entities:
                    name = entity.get("name", "")
                    entity_type = entity.get("entity_type", "")
                    if name and entity_type and entity_type != "file":  # Skip file entities
                        key = (name, entity_type)
                        if key not in entity_registry:
                            entity_registry[key] = []
                        entity_registry[key].append(entity)
                
                relationships.extend(file_relationships)
            except SyntaxError as e:
                error_msg = f"Syntax error: {e}"
                logger.warning(f"Failed to index {relative_path}: {error_msg}")
                failed_files.append((relative_path, error_msg))
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.warning(f"Failed to index {relative_path}: {error_msg}")
                failed_files.append((relative_path, error_msg))
        
        # Second pass: Resolve cross-file relationships
        resolved_relationships = []
        for rel in relationships:
            # If target_entity_id is None, try to resolve it using registry
            if rel.get("target_entity_id") is None:
                target_symbol = rel.get("target_symbol_name", "")
                relationship_type = rel.get("relationship_type", "")
                
                # Try to find target entity in registry
                if relationship_type in ("calls", "CALLS_FUNCTION"):
                    # Look for function or method
                    for entity_type in ["function", "function_definition", "method_definition"]:
                        key = (target_symbol, entity_type)
                        if key in entity_registry:
                            # Use first match (could be improved with scope resolution)
                            target_entity = entity_registry[key][0]
                            rel["target_entity_id"] = target_entity["entity_id"]
                            break
                
                elif relationship_type in ("IMPORTS", "imports"):
                    # Look for imported entity (function, class, or module)
                    for entity_type in ["function", "function_definition", "class_definition", "class"]:
                        key = (target_symbol, entity_type)
                        if key in entity_registry:
                            target_entity = entity_registry[key][0]
                            rel["target_entity_id"] = target_entity["entity_id"]
                            break
                
                elif relationship_type in ("EXTENDS_CLASS", "extends"):
                    # Look for class
                    for entity_type in ["class_definition", "class"]:
                        key = (target_symbol, entity_type)
                        if key in entity_registry:
                            target_entity = entity_registry[key][0]
                            rel["target_entity_id"] = target_entity["entity_id"]
                            break
            
            resolved_relationships.append(rel)
        
        relationships = resolved_relationships

        status = "completed" if not failed_files else "completed_with_errors"
        
        return {
            "indexed": len(entities),
            "entities_by_type": self._count_by_type(entities),
            "languages": languages,
            "relationships": len(relationships),
            "status": status,
            "entities": entities,
            "relationships": relationships,
            "failed_files": failed_files,
        }

    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped."""
        # Skip node_modules, venv, .git, etc.
        skip_dirs = {"node_modules", "venv", ".git", "__pycache__", "dist", "build"}
        parts = file_path.parts
        return any(part in skip_dirs for part in parts)

    def _index_file(
        self, 
        file_path: str, 
        language: str,
        entity_registry: Optional[Dict[tuple, List[Dict[str, Any]]]] = None
    ) -> tuple[List[Dict], List[Dict]]:
        """Index a single file using tree-sitter parser."""
        full_path = self.repo_path / file_path

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            return [], []

        # Use tree-sitter parser if available
        if self.parser_initialized:
            parser = self.tree_sitter_manager.get_parser_for_language(language)
            if parser:
                try:
                    tree = parser.parse(bytes(content, "utf-8"))
                    ast_root = tree.root_node

                    # Use language-specific parser with entity registry
                    if language == "python":
                        result = parse_python(ast_root, content, entity_registry)
                    elif language == "javascript":
                        result = parse_javascript(ast_root, content, entity_registry)
                    elif language in ("typescript", "tsx"):
                        result = parse_typescript(ast_root, content, entity_registry)
                    else:
                        return [], []

                    # Add file entity and update file paths
                    file_entity = {
                        "entity_id": f"{file_path}:file",
                        "file_path": file_path,
                        "entity_type": "file",
                        "name": Path(file_path).name,
                        "start_line": 1,
                        "end_line": len(content.splitlines()),
                        "language": language,
                    }
                    result["entities"].insert(0, file_entity)

                    # Update file paths for all entities
                    for entity in result["entities"]:
                        if "file_path" not in entity:
                            entity["file_path"] = file_path

                    return result["entities"], result["relationships"]
                except Exception as e:
                    logger.warning(f"Tree-sitter parsing failed for {file_path}: {e}. Falling back to basic parsing.")

        # Fallback to basic parsing
        if language == "python":
            return self._index_python_file_fallback(file_path, full_path)
        elif language in ("javascript", "typescript"):
            return self._index_js_file_fallback(file_path, full_path, language)
        else:
            return [], []

    def _index_python_file_fallback(
        self, file_path: str, full_path: Path
    ) -> tuple[List[Dict], List[Dict]]:
        """Fallback Python file indexing using standard library AST."""
        import ast

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

    def _index_js_file_fallback(
        self, file_path: str, full_path: Path, language: str
    ) -> tuple[List[Dict], List[Dict]]:
        """Fallback JavaScript/TypeScript file indexing using regex (simplified)."""
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

            # Simple regex-based extraction (fallback only)
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
