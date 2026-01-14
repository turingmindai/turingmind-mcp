#!/usr/bin/env python3
"""
Test script to verify graph generation on turingmind-mcp codebase.

Tests:
1. Indexing the codebase
2. Storing entities and relationships
3. Querying the graph
4. Getting related code
"""

import sys
import os
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import directly from source files to avoid __init__ imports
import importlib.util

# Load database module
db_spec = importlib.util.spec_from_file_location(
    "database", src_path / "turingmind_mcp" / "database.py"
)
database = importlib.util.module_from_spec(db_spec)
db_spec.loader.exec_module(database)

# Load entity_indexer module
indexer_spec = importlib.util.spec_from_file_location(
    "entity_indexer", src_path / "turingmind_mcp" / "entity_indexer.py"
)
entity_indexer = importlib.util.module_from_spec(indexer_spec)
indexer_spec.loader.exec_module(entity_indexer)

MemoryDatabase = database.MemoryDatabase
EntityIndexer = entity_indexer.EntityIndexer
get_repo_path = entity_indexer.get_repo_path


def test_indexing():
    """Test indexing the codebase."""
    print("=" * 80)
    print("TEST 1: Indexing Codebase")
    print("=" * 80)

    repo_path = get_repo_path()
    if not repo_path:
        print("❌ Could not determine repository path")
        return None

    print(f"📁 Repository path: {repo_path}")

    indexer = EntityIndexer(str(repo_path))
    result = indexer.index_codebase(languages=["python"], force_reindex=False)

    print(f"\n✅ Indexing Complete:")
    print(f"   - Entities indexed: {result['indexed']}")
    print(f"   - Relationships: {len(result.get('relationships', []))}")
    print(f"   - Entity types: {result['entities_by_type']}")

    # Show sample entities
    entities = result.get("entities", [])
    if entities:
        print(f"\n📋 Sample Entities (first 5):")
        for i, entity in enumerate(entities[:5], 1):
            print(f"   {i}. {entity.get('entity_type', 'unknown')}: {entity.get('name', 'unnamed')}")
            print(f"      File: {entity.get('file_path', 'unknown')}")
            print(f"      Lines: {entity.get('start_line', '?')}-{entity.get('end_line', '?')}")

    # Show sample relationships
    relationships = result.get("relationships", [])
    if relationships:
        print(f"\n🔗 Sample Relationships (first 5):")
        for i, rel in enumerate(relationships[:5], 1):
            print(f"   {i}. {rel.get('relationship_type', 'unknown')}")
            print(f"      Source: {rel.get('source_entity_id', 'unknown')[:50]}...")
            print(f"      Target: {rel.get('target_symbol_name', 'unknown')}")

    return result


def test_database_storage(result):
    """Test storing entities and relationships in database."""
    print("\n" + "=" * 80)
    print("TEST 2: Storing in Database")
    print("=" * 80)

    if not result:
        print("❌ No indexing result to store")
        return None

    # Use test database
    db_path = Path.home() / ".turingmind" / "test_graph.db"
    db = MemoryDatabase(db_path=str(db_path))
    print(f"📦 Database: {db_path}")

    repo = "turingmind-ai/turingmind-mcp"
    entity_id_map = {}

    # Store entities
    print("\n💾 Storing entities...")
    entities = result.get("entities", [])
    stored_count = 0
    for entity in entities:
        try:
            db_entity_id = db.create_code_entity(
                repo=repo,
                file_path=entity.get("file_path", ""),
                entity_type=entity.get("entity_type", "unknown"),
                name=entity.get("name", ""),
                start_line=entity.get("start_line"),
                end_line=entity.get("end_line"),
                language=entity.get("language", "python"),
            )
            key = (
                entity.get("file_path", ""),
                entity.get("name", ""),
                entity.get("entity_type", "unknown"),
            )
            entity_id_map[key] = db_entity_id
            stored_count += 1
        except Exception as e:
            print(f"   ⚠️  Error storing entity {entity.get('name', 'unknown')}: {e}")

    print(f"   ✅ Stored {stored_count} entities")

    # Store relationships
    print("\n💾 Storing relationships...")
    relationships = result.get("relationships", [])
    stored_rel_count = 0
    for rel in relationships:
        try:
            source_entity_id_str = rel.get("source_entity_id", "")
            source_id = None

            # Try to find source entity
            if source_entity_id_str in entity_id_map:
                source_id = entity_id_map[source_entity_id_str]
            elif ":" in source_entity_id_str:
                parts = source_entity_id_str.split(":")
                if len(parts) >= 3:
                    file_path = parts[0]
                    name = parts[1]
                    entity_type = ":".join(parts[2:])
                    key = (file_path, name, entity_type)
                    source_id = entity_id_map.get(key)

            if source_id:
                db.create_relationship(
                    repo=repo,
                    source_entity_id=source_id,
                    target_entity_id=None,  # Target may be in another file
                    target_symbol_name=rel.get("target_symbol_name", ""),
                    relationship_type=rel.get("relationship_type", "calls"),
                )
                stored_rel_count += 1
        except Exception as e:
            print(f"   ⚠️  Error storing relationship: {e}")

    print(f"   ✅ Stored {stored_rel_count} relationships")

    return db, repo


def test_graph_queries(db, repo):
    """Test querying the graph."""
    print("\n" + "=" * 80)
    print("TEST 3: Querying Graph")
    print("=" * 80)

    # Get project structure
    print("\n📊 Project Structure:")
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT entity_type, language, COUNT(*) as count
        FROM code_entities
        WHERE repo = ?
        GROUP BY entity_type, language
        ORDER BY count DESC
        """,
        (repo,),
    )
    stats = cursor.fetchall()

    for row in stats:
        entity_type, language, count = row
        print(f"   - {entity_type} ({language or 'unknown'}): {count}")

    # Get entities for a specific file
    print("\n📁 Entities in server.py:")
    entities = db.get_entities_by_file(repo, "src/turingmind_mcp/server.py")
    print(f"   Found {len(entities)} entities")
    for entity in entities[:10]:
        print(f"   - {entity['entity_type']}: {entity['name']} (lines {entity.get('start_line', '?')}-{entity.get('end_line', '?')})")

    # Get relationships
    print("\n🔗 Sample Relationships:")
    cursor.execute(
        """
        SELECT r.relationship_type, r.target_symbol_name, 
               e1.file_path as source_file, e1.name as source_name,
               e2.file_path as target_file, e2.name as target_name
        FROM code_relationships r
        JOIN code_entities e1 ON r.source_entity_id = e1.entity_id
        LEFT JOIN code_entities e2 ON r.target_entity_id = e2.entity_id
        WHERE e1.repo = ?
        LIMIT 10
        """,
        (repo,),
    )
    rels = cursor.fetchall()

    for rel in rels:
        rel_type, target_symbol, src_file, src_name, tgt_file, tgt_name = rel
        print(f"   - {rel_type}: {src_file}:{src_name} -> {target_symbol or (tgt_file + ':' + tgt_name if tgt_name else 'external')}")

    return entities


def test_related_code(db, repo, entities):
    """Test getting related code."""
    print("\n" + "=" * 80)
    print("TEST 4: Getting Related Code")
    print("=" * 80)

    if not entities:
        print("❌ No entities to test with")
        return

    # Test with first function/class entity
    test_entity = None
    for entity in entities:
        if entity["entity_type"] in ("function", "class", "function_definition", "class_definition"):
            test_entity = entity
            break

    if not test_entity:
        print("❌ No function/class entity found")
        return

    print(f"\n🔍 Testing with entity: {test_entity['name']} ({test_entity['entity_type']})")

    # Get related entities
    related = db.get_related_entities(
        test_entity["entity_id"],
        relationship_types=["calls", "imports", "DEFINES_CHILD_ENTITY"],
        direction="both",
    )

    print(f"\n   Found {len(related)} related entities:")

    # Group by relationship type
    by_type = {}
    for rel in related:
        rel_type = rel.get("relationship_type", "unknown")
        if rel_type not in by_type:
            by_type[rel_type] = []
        by_type[rel_type].append(rel)

    for rel_type, rels in by_type.items():
        print(f"\n   {rel_type} ({len(rels)}):")
        for rel in rels[:5]:
            print(f"      - {rel.get('file_path', 'unknown')}:{rel.get('name', 'unknown')}")


def main():
    """Run all tests."""
    print("\n" + "🧪 " * 20)
    print("Testing Graph Generation on turingmind-mcp")
    print("🧪 " * 20 + "\n")

    try:
        # Test 1: Indexing
        result = test_indexing()

        # Test 2: Database storage
        db, repo = test_database_storage(result)
        if not db:
            return

        # Test 3: Graph queries
        entities = test_graph_queries(db, repo)

        # Test 4: Related code
        test_related_code(db, repo, entities)

        print("\n" + "=" * 80)
        print("✅ All Tests Complete!")
        print("=" * 80)
        print(f"\nDatabase saved at: {db.db_path}")
        print("You can query it using the MCP tools or directly via SQLite.")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
