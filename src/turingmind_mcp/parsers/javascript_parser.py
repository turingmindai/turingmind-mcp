"""
JavaScript parser using tree-sitter.

Extracts code entities and relationships from JavaScript/TypeScript AST.
Similar to DevContext's javascript.parser.js implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("turingmind-mcp")


def parse_javascript(
    ast_root_node: Any, 
    file_content: str,
    entity_registry: Optional[Dict[tuple, List[Dict[str, Any]]]] = None
) -> Dict[str, Any]:
    """
    Extract code entities and relationships from a JavaScript AST.

    Args:
        ast_root_node: The root node of the tree-sitter AST
        file_content: The full content of the file
        entity_registry: Optional registry of entities from other files for cross-file lookups

    Returns:
        Dictionary containing 'entities' and 'relationships' lists
    """
    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []

    # Track current parent entity
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
        start_line = node.start_point[0] + 1
        start_column = node.start_point[1]
        end_line = node.end_point[0] + 1
        end_column = node.end_point[1]

        # Extract raw content
        start_byte = node.start_byte
        end_byte = node.end_byte
        raw_content = file_content[start_byte:end_byte]

        # Determine language
        language = (
            "typescript"
            if any(
                t in entity_type
                for t in ["interface", "type_alias", "enum", "type_parameter"]
            )
            else "javascript"
        )

        entity_id = f"js-{entity_type}-{start_line}-{start_column}"

        entity = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "name": name,
            "start_line": start_line,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "raw_content": raw_content,
            "language": language,
            "parent_entity_id": current_parent_entity["entity_id"] if current_parent_entity else None,
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
        """Find an entity by name."""
        # First search in current file's entities
        for entity in entities:
            if entity["name"] == name and (entity_type is None or entity["entity_type"] == entity_type):
                return entity
        
        # Then search in global registry if available
        if entity_registry:
            if entity_type:
                key = (name, entity_type)
                if key in entity_registry:
                    # Return first match (could be improved with scope resolution)
                    return entity_registry[key][0]
            else:
                # Try common entity types
                for et in ["function", "function_declaration", "class_declaration", "class"]:
                    key = (name, et)
                    if key in entity_registry:
                        return entity_registry[key][0]
        
        return None

    def process_function_declaration(node: Any):
        """Process a function declaration node."""
        nonlocal current_parent_entity
        
        name_node = None
        is_async = False

        # Check for async keyword
        for child in node.children:
            if child.type == "async":
                is_async = True
            elif child.type == "identifier":
                name_node = child
                break

        if name_node:
            function_name = get_text(name_node)
            custom_metadata = {"is_async": is_async}

            entity = create_entity(node, "function_declaration", function_name, custom_metadata)
            entities.append(entity)

            # Set as parent and process body
            prev_parent = current_parent_entity
            current_parent_entity = entity

            # Process function body
            for child in node.children:
                if child.type == "statement_block":
                    traverse_node(child)
                    process_function_calls(child, entity)
                    process_variable_references(child, entity)

            current_parent_entity = prev_parent

    def process_class_declaration(node: Any):
        """Process a class declaration node."""
        nonlocal current_parent_entity
        
        name_node = None
        for child in node.children:
            if child.type == "class_heritage":
                # Process extends clause
                for heritage_child in child.children:
                    if heritage_child.type == "extends_clause":
                        for extends_child in heritage_child.children:
                            if extends_child.type == "identifier":
                                base_name = get_text(extends_child)
                                target_entity = find_entity_by_name(base_name, "class_declaration")
                                # Will create relationship after entity is created
                                break
            elif child.type == "identifier":
                name_node = child
                break

        if name_node:
            class_name = get_text(name_node)

            entity = create_entity(node, "class_declaration", class_name)
            entities.append(entity)

            # Process inheritance relationships
            process_class_inheritance(node, entity)

            # Set as parent and process body
            prev_parent = current_parent_entity
            current_parent_entity = entity

            # Process class body
            for child in node.children:
                if child.type == "class_body":
                    traverse_node(child)

            current_parent_entity = prev_parent

    def process_class_inheritance(node: Any, class_entity: Dict[str, Any]):
        """Process class inheritance relationships."""
        for child in node.children:
            if child.type == "class_heritage":
                for heritage_child in child.children:
                    if heritage_child.type == "extends_clause":
                        for extends_child in heritage_child.children:
                            if extends_child.type == "identifier":
                                base_name = get_text(extends_child)
                                target_entity = find_entity_by_name(base_name, "class_declaration")

                                create_relationship(
                                    class_entity["entity_id"],
                                    target_entity["entity_id"] if target_entity else None,
                                    base_name,
                                    "EXTENDS_CLASS",
                                )

    def process_import_statement(node: Any):
        """Process an import statement."""
        import_specifiers = []
        from_module = None

        for child in node.children:
            if child.type == "import_clause":
                for clause_child in child.children:
                    if clause_child.type == "import_specifier":
                        for spec_child in clause_child.children:
                            if spec_child.type == "identifier":
                                import_name = get_text(spec_child)
                                import_specifiers.append(import_name)
            elif child.type == "string":
                # Module path
                from_module = get_text(child)
                # Remove quotes
                from_module = from_module.strip('"').strip("'")

        if import_specifiers or from_module:
            import_name = ", ".join(import_specifiers) if import_specifiers else from_module or "import"
            custom_metadata = {
                "imported_names": import_specifiers,
                "from_module": from_module,
            }

            entity = create_entity(node, "import_statement", import_name, custom_metadata)
            entities.append(entity)

            # Create import relationships
            for imported_name in import_specifiers:
                create_relationship(
                    entity["entity_id"],
                    None,
                    imported_name,
                    "IMPORTS",
                    {"from_module": from_module} if from_module else {},
                )

    def process_function_calls(node: Any, scope_entity: Dict[str, Any]):
        """Process function calls within a code block."""
        if not node or not scope_entity:
            return

        if node.type == "call_expression":
            function_name = ""
            metadata = {}

            function_node = node.children[0] if node.children else None

            if function_node:
                if function_node.type == "identifier":
                    function_name = get_text(function_node)
                elif function_node.type == "member_expression":
                    # Method call: obj.method()
                    for member_child in function_node.children:
                        if member_child.type == "property_identifier":
                            function_name = get_text(member_child)
                            # Get object name
                            obj_node = function_node.children[0] if function_node.children else None
                            if obj_node and obj_node.type == "identifier":
                                metadata["object_name"] = get_text(obj_node)
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
            target_entity = find_entity_by_name(var_name, "variable_declarator")

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

        if node_type == "function_declaration":
            process_function_declaration(node)
        elif node_type == "class_declaration":
            process_class_declaration(node)
        elif node_type == "lexical_declaration":
            # const/let declarations
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = None
                    for var_child in child.children:
                        if var_child.type == "identifier":
                            name_node = var_child
                            break
                    if name_node:
                        var_name = get_text(name_node)
                        entity = create_entity(node, "variable_declarator", var_name)
                        entities.append(entity)
        elif node_type == "import_statement":
            process_import_statement(node)
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
