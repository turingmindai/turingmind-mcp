"""
Python parser using tree-sitter.

Extracts code entities and relationships from Python AST.
Similar to DevContext's python.parser.js implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("turingmind-mcp")


def parse_python(ast_root_node: Any, file_content: str) -> Dict[str, Any]:
    """
    Extract code entities and relationships from a Python AST.

    Args:
        ast_root_node: The root node of the tree-sitter AST
        file_content: The full content of the file

    Returns:
        Dictionary containing 'entities' and 'relationships' lists
    """
    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []

    # Track current parent entity for hierarchical relationships
    current_parent_entity: Optional[Dict[str, Any]] = None

    def get_text(node: Any) -> str:
        """Extract text from tree-sitter node."""
        if hasattr(node, "text"):
            text = node.text
            if isinstance(text, bytes):
                return text.decode("utf-8")
            return text
        # Fallback: extract from file content using byte positions
        return file_content[node.start_byte : node.end_byte]

    def create_entity(
        node: Any,
        entity_type: str,
        name: str,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a code entity object."""
        if custom_metadata is None:
            custom_metadata = {}

        # Extract position information
        start_line = node.start_point[0] + 1  # tree-sitter uses 0-based, we use 1-based
        start_column = node.start_point[1]
        end_line = node.end_point[0] + 1
        end_column = node.end_point[1]

        # Extract raw content
        start_byte = node.start_byte
        end_byte = node.end_byte
        raw_content = file_content[start_byte:end_byte]

        # Create unique ID
        entity_id = f"python-{entity_type}-{start_line}-{start_column}-{end_line}-{end_column}"

        entity = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "name": name,
            "language": "python",
            "start_line": start_line,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "raw_content": raw_content,
            "custom_metadata": custom_metadata,
        }

        # Create parent-child relationship if parent exists
        if current_parent_entity:
            create_relationship(
                current_parent_entity["entity_id"],
                entity_id,
                name,
                "DEFINES_CHILD_ENTITY",
            )

        return entity

    def create_relationship(
        source_entity_id: str,
        target_entity_id: Optional[str],
        target_symbol_name: str,
        relationship_type: str,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a code relationship object."""
        if custom_metadata is None:
            custom_metadata = {}

        relationship = {
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "target_symbol_name": target_symbol_name,
            "relationship_type": relationship_type,
            "custom_metadata": custom_metadata,
        }

        relationships.append(relationship)
        return relationship

    def find_entity_by_name(name: str, entity_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find an entity by name and optionally by type."""
        for entity in entities:
            if entity["name"] == name and (entity_type is None or entity["entity_type"] == entity_type):
                return entity
        return None

    def process_function_definition(node: Any):
        """Process a function definition node."""
        # Find the identifier (function name)
        name_node = None
        is_async = False

        # Check for async keyword
        if node.children and node.children[0].type == "async":
            is_async = True

        # Find identifier child
        for child in node.children:
            if child.type == "identifier":
                name_node = child
                break

        if name_node:
            function_name = get_text(name_node)
            custom_metadata = {
                "is_async": is_async,
                "is_method": (
                    current_parent_entity is not None
                    and current_parent_entity.get("entity_type") == "class_definition"
                ),
            }

            entity = create_entity(
                node,
                "method_definition" if custom_metadata["is_method"] else "function_definition",
                function_name,
                custom_metadata,
            )
            entities.append(entity)

            # Set as parent and process body
            prev_parent = current_parent_entity
            nonlocal current_parent_entity
            current_parent_entity = entity

            # Process function body
            for child in node.children:
                if child.type == "block":
                    traverse_node(child)
                    process_function_calls(child, entity)
                    process_variable_references(child, entity)

            current_parent_entity = prev_parent

    def process_class_definition(node: Any):
        """Process a class definition node."""
        # Find identifier (class name)
        name_node = None
        for child in node.children:
            if child.type == "identifier":
                name_node = child
                break

        if name_node:
            class_name = get_text(name_node)

            # Check for inheritance
            base_classes = []
            for child in node.children:
                if child.type == "argument_list":
                    for base_child in child.children:
                        if base_child.type == "identifier":
                            base_name = get_text(base_child)
                            base_classes.append(base_name)

            custom_metadata = {"base_classes": base_classes}

            entity = create_entity(node, "class_definition", class_name, custom_metadata)
            entities.append(entity)

            # Process inheritance relationships
            process_class_inheritance(node, entity)

            # Set as parent and process body
            prev_parent = current_parent_entity
            nonlocal current_parent_entity
            current_parent_entity = entity

            # Process class body
            for child in node.children:
                if child.type == "block":
                    traverse_node(child)

            current_parent_entity = prev_parent

    def process_class_inheritance(node: Any, class_entity: Dict[str, Any]):
        """Process class inheritance relationships."""
        for child in node.children:
            if child.type == "argument_list":
                for base_child in child.children:
                    if base_child.type == "identifier":
                        base_name = base_child.text.decode("utf-8") if hasattr(base_child.text, "decode") else base_child.text
                        target_entity = find_entity_by_name(base_name, "class_definition")

                        create_relationship(
                            class_entity["entity_id"],
                            target_entity["entity_id"] if target_entity else None,
                            base_name,
                            "EXTENDS_CLASS",
                        )

    def process_import(node: Any):
        """Process an import statement."""
        import_names = []
        from_module = None

        for child in node.children:
            if child.type == "dotted_name":
                import_name = get_text(child)
                import_names.append(import_name)
            elif child.type == "aliased_import":
                # Handle import with alias: import foo as bar
                for alias_child in child.children:
                    if alias_child.type == "dotted_name":
                        name = get_text(alias_child)
                        import_names.append(name)
                        break
            elif child.type == "identifier" and node.type == "import_from_statement":
                import_name = get_text(child)
                import_names.append(import_name)

            # Handle 'from' part
            if child.type == "dotted_name" and node.type == "import_from_statement":
                from_module = get_text(child)

        if import_names:
            import_name = ", ".join(import_names)
            custom_metadata = {
                "imported_names": import_names,
                "from_module": from_module,
            }

            entity = create_entity(
                node,
                "import_from" if from_module else "import",
                import_name,
                custom_metadata,
            )
            entities.append(entity)

            # Create import relationships
            process_import_relationships(entity)

    def process_import_relationships(entity: Dict[str, Any]):
        """Process import relationships."""
        imported_names = entity.get("custom_metadata", {}).get("imported_names", [])
        from_module = entity.get("custom_metadata", {}).get("from_module")

        for imported_name in imported_names:
            # Create relationship to imported symbol
            create_relationship(
                entity["entity_id"],
                None,  # Target may be in another file
                imported_name,
                "IMPORTS",
                {"from_module": from_module} if from_module else {},
            )

    def process_function_calls(node: Any, scope_entity: Dict[str, Any]):
        """Process function calls within a code block."""
        if not node or not scope_entity:
            return

        if node.type == "call":
            function_name = ""
            metadata = {}

            # Extract function name from call node
            function_node = None
            for child in node.children:
                if child.type in ("identifier", "attribute"):
                    function_node = child
                    break

            if function_node:
                if function_node.type == "identifier":
                    function_name = get_text(function_node)
                elif function_node.type == "attribute":
                    # Method call: obj.method()
                    for attr_child in function_node.children:
                        if attr_child.type == "identifier":
                            function_name = get_text(attr_child)
                            break

                if function_name:
                    target_entity = find_entity_by_name(function_name)

                    create_relationship(
                        scope_entity["entity_id"],
                        target_entity["entity_id"] if target_entity else None,
                        function_name,
                        "CALLS_FUNCTION",
                        {
                            "call_location": {
                                "line": node.start_point[0] + 1,
                                "column": node.start_point[1],
                            },
                            **metadata,
                        },
                    )

        # Recursively process children
        for child in node.children:
            process_function_calls(child, scope_entity)

    def process_variable_references(node: Any, scope_entity: Dict[str, Any]):
        """Process variable references within a scope."""
        if not node or not scope_entity:
            return

        # Process identifiers that might be variable references
        if node.type == "identifier":
            var_name = get_text(node)
            target_entity = find_entity_by_name(var_name, "assignment")

            if target_entity:
                create_relationship(
                    scope_entity["entity_id"],
                    target_entity["entity_id"],
                    var_name,
                    "REFERENCES_VARIABLE",
                    {
                        "reference_location": {
                            "line": node.start_point[0] + 1,
                            "column": node.start_point[1],
                        },
                    },
                )

        # Recursively process children
        for child in node.children:
            process_variable_references(child, scope_entity)

    def traverse_node(node: Any):
        """Traverse AST node and extract entities."""
        if not node:
            return

        node_type = node.type

        if node_type == "function_definition":
            process_function_definition(node)
        elif node_type == "class_definition":
            process_class_definition(node)
        elif node_type in ("import_statement", "import_from_statement"):
            process_import(node)
        else:
            # Recursively traverse children
            for child in node.children:
                traverse_node(child)

    # Traverse the AST
    traverse_node(ast_root_node)

    return {
        "entities": entities,
        "relationships": relationships,
    }
