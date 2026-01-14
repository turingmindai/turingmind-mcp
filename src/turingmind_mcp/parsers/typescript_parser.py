"""
TypeScript parser using tree-sitter.

Extracts code entities and relationships from TypeScript AST.
Uses the same logic as JavaScript parser but handles TypeScript-specific constructs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .javascript_parser import parse_javascript

logger = logging.getLogger("turingmind-mcp")


def parse_typescript(ast_root_node: Any, file_content: str) -> Dict[str, Any]:
    """
    Extract code entities and relationships from a TypeScript AST.

    Args:
        ast_root_node: The root node of the tree-sitter AST
        file_content: The full content of the file

    Returns:
        Dictionary containing 'entities' and 'relationships' lists
    """
    # TypeScript parsing is similar to JavaScript, but we need to handle
    # TypeScript-specific constructs like interfaces, type aliases, enums

    # Use JavaScript parser as base
    result = parse_javascript(ast_root_node, file_content)

    # Add TypeScript-specific processing
    entities = result["entities"]
    relationships = result["relationships"]

    def get_text(node: Any) -> str:
        """Extract text from tree-sitter node."""
        if hasattr(node, "text"):
            text = node.text
            if isinstance(text, bytes):
                return text.decode("utf-8")
            return text
        return file_content[node.start_byte : node.end_byte]

    def process_interface_declaration(node: Any):
        """Process a TypeScript interface declaration."""
        name_node = None
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break

        if name_node:
            interface_name = get_text(name_node)

            start_line = node.start_point[0] + 1
            start_column = node.start_point[1]
            end_line = node.end_point[0] + 1
            end_column = node.end_point[1]

            entity_id = f"ts-interface-{start_line}-{start_column}"

            entity = {
                "entity_id": entity_id,
                "entity_type": "interface_declaration",
                "name": interface_name,
                "language": "typescript",
                "start_line": start_line,
                "start_column": start_column,
                "end_line": end_line,
                "end_column": end_column,
                "raw_content": file_content[node.start_byte : node.end_byte],
                "custom_metadata": {},
            }

            entities.append(entity)

    def process_type_alias(node: Any):
        """Process a TypeScript type alias."""
        name_node = None
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break

        if name_node:
            type_name = get_text(name_node)

            start_line = node.start_point[0] + 1
            start_column = node.start_point[1]
            end_line = node.end_point[0] + 1
            end_column = node.end_point[1]

            entity_id = f"ts-type-{start_line}-{start_column}"

            entity = {
                "entity_id": entity_id,
                "entity_type": "type_alias",
                "name": type_name,
                "language": "typescript",
                "start_line": start_line,
                "start_column": start_column,
                "end_line": end_line,
                "end_column": end_column,
                "raw_content": file_content[node.start_byte : node.end_byte],
                "custom_metadata": {},
            }

            entities.append(entity)

    def traverse_typescript_nodes(node: Any):
        """Traverse AST for TypeScript-specific nodes."""
        if not node:
            return

        node_type = node.type

        if node_type == "interface_declaration":
            process_interface_declaration(node)
        elif node_type == "type_alias_declaration":
            process_type_alias(node)
        else:
            # Recursively traverse children
            for child in node.children:
                traverse_typescript_nodes(child)

    # Process TypeScript-specific constructs
    traverse_typescript_nodes(ast_root_node)

    return {
        "entities": entities,
        "relationships": relationships,
    }
