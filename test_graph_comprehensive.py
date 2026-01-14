#!/usr/bin/env python3
"""
Comprehensive test for graph generation on turingmind-mcp codebase.
Uses fallback Python AST parser (works without tree-sitter).
"""

import sys
import sqlite3
import tempfile
import uuid
from pathlib import Path
from collections import defaultdict

def test_indexing_with_ast():
    """Test indexing using Python's built-in AST parser."""
    repo_path = Path(__file__).parent
    src_path = repo_path / "src" / "turingmind_mcp"
    
    print("=" * 80)
    print("TEST: Indexing Codebase with Python AST Parser")
    print("=" * 80)
    
    entities = []
    relationships = []
    
    # Find all Python files
    python_files = list(src_path.rglob("*.py"))
    print(f"\n📁 Found {len(python_files)} Python files to index\n")
    
    import ast
    
    for py_file in python_files:
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            file_path = str(py_file.relative_to(repo_path))
            tree = ast.parse(content, filename=str(py_file))
            
            # Add file entity
            entities.append({
                "entity_id": f"{file_path}:file",
                "file_path": file_path,
                "entity_type": "file",
                "name": py_file.name,
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
                    
                    # Extract function calls
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                target_name = child.func.id
                                relationships.append({
                                    "source_entity_id": entity_id,
                                    "target_symbol_name": target_name,
                                    "relationship_type": "calls",
                                })
                            elif isinstance(child.func, ast.Attribute):
                                # Method call: obj.method()
                                target_name = child.func.attr
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
                    
                    # Extract base classes
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            relationships.append({
                                "source_entity_id": entity_id,
                                "target_symbol_name": base.id,
                                "relationship_type": "extends",
                            })
                    
                    # Extract method calls
                    for item in node.body:
                        if isinstance(node, ast.FunctionDef):
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
                
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        relationships.append({
                            "source_entity_id": f"{file_path}:file",
                            "target_symbol_name": alias.name,
                            "relationship_type": "imports",
                        })
                
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        relationships.append({
                            "source_entity_id": f"{file_path}:file",
                            "target_symbol_name": f"{module}.{alias.name}" if module else alias.name,
                            "relationship_type": "imports",
                        })
        
        except SyntaxError as e:
            print(f"   ⚠️  Syntax error in {py_file.name}: {e}")
        except Exception as e:
            print(f"   ⚠️  Error processing {py_file.name}: {e}")
    
    # Count by type
    by_type = defaultdict(int)
    for entity in entities:
        by_type[entity["entity_type"]] += 1
    
    print(f"✅ Indexing Complete:")
    print(f"   - Total entities: {len(entities)}")
    print(f"   - Relationships: {len(relationships)}")
    print(f"   - Entity types: {dict(by_type)}")
    
    return entities, relationships


def test_database_storage(entities, relationships):
    """Test storing in database."""
    print("\n" + "=" * 80)
    print("TEST: Storing in Database")
    print("=" * 80)
    
    # Create temporary database
    db_path = Path.home() / ".turingmind" / "test_graph.db"
    db_path.parent.mkdir(exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_entities (
            entity_id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            file_path TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            start_line INTEGER,
            end_line INTEGER,
            language TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_relationships (
            relationship_id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            source_entity_id TEXT NOT NULL,
            target_entity_id TEXT,
            target_symbol_name TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            FOREIGN KEY (source_entity_id) REFERENCES code_entities(entity_id)
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_repo_file ON code_entities(repo, file_path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_source ON code_relationships(source_entity_id)")
    
    repo = "turingmind-ai/turingmind-mcp"
    entity_id_map = {}
    
    # Store entities
    print("\n💾 Storing entities...")
    stored_count = 0
    for entity in entities:
        entity_id = entity["entity_id"]
        cursor.execute("""
            INSERT OR REPLACE INTO code_entities 
            (entity_id, repo, file_path, entity_type, name, start_line, end_line, language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entity_id,
            repo,
            entity["file_path"],
            entity["entity_type"],
            entity["name"],
            entity.get("start_line"),
            entity.get("end_line"),
            entity.get("language", "python"),
        ))
        entity_id_map[entity_id] = entity_id
        stored_count += 1
    
    print(f"   ✅ Stored {stored_count} entities")
    
    # Store relationships
    print("\n💾 Storing relationships...")
    stored_rel_count = 0
    for rel in relationships:
        source_id = rel.get("source_entity_id")
        if source_id in entity_id_map:
            rel_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO code_relationships
                (relationship_id, repo, source_entity_id, target_entity_id, target_symbol_name, relationship_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                rel_id,
                repo,
                source_id,
                None,  # Target may be external
                rel.get("target_symbol_name", ""),
                rel.get("relationship_type", "calls"),
            ))
            stored_rel_count += 1
    
    conn.commit()
    print(f"   ✅ Stored {stored_rel_count} relationships")
    
    return conn, repo


def test_graph_queries(conn, repo):
    """Test querying the graph."""
    print("\n" + "=" * 80)
    print("TEST: Querying Graph")
    print("=" * 80)
    
    cursor = conn.cursor()
    
    # Project structure
    print("\n📊 Project Structure:")
    cursor.execute("""
        SELECT entity_type, COUNT(*) as count
        FROM code_entities
        WHERE repo = ?
        GROUP BY entity_type
        ORDER BY count DESC
    """, (repo,))
    
    for row in cursor.fetchall():
        print(f"   - {row[0]}: {row[1]}")
    
    # Entities in server.py
    print("\n📁 Entities in server.py:")
    cursor.execute("""
        SELECT entity_type, name, start_line, end_line
        FROM code_entities
        WHERE repo = ? AND file_path = 'src/turingmind_mcp/server.py'
        ORDER BY start_line
        LIMIT 15
    """, (repo,))
    
    for row in cursor.fetchall():
        print(f"   - {row[1]} ({row[0]}) lines {row[2]}-{row[3]}")
    
    # Sample relationships
    print("\n🔗 Sample Relationships:")
    cursor.execute("""
        SELECT r.relationship_type, r.target_symbol_name, 
               e1.file_path as source_file, e1.name as source_name
        FROM code_relationships r
        JOIN code_entities e1 ON r.source_entity_id = e1.entity_id
        WHERE e1.repo = ?
        LIMIT 15
    """, (repo,))
    
    for row in cursor.fetchall():
        print(f"   - {row[3]} ({row[0]}) -> {row[1]}")
    
    # Get related entities for a function
    print("\n🔍 Related Code for 'call_tool' function:")
    cursor.execute("""
        SELECT e2.name, e2.file_path, r.relationship_type
        FROM code_entities e1
        JOIN code_relationships r ON e1.entity_id = r.source_entity_id
        LEFT JOIN code_entities e2 ON r.target_entity_id = e2.entity_id
        WHERE e1.repo = ? AND e1.name = 'call_tool' AND e1.entity_type = 'function'
        LIMIT 10
    """, (repo,))
    
    related = cursor.fetchall()
    if related:
        for row in related:
            print(f"   - {row[2]}: {row[1] or 'external'}:{row[0] or 'unknown'}")
    else:
        print("   (No relationships found - may need to match by symbol name)")
    
    return cursor


def main():
    """Run all tests."""
    print("\n" + "🧪 " * 20)
    print("Comprehensive Graph Generation Test")
    print("🧪 " * 20 + "\n")
    
    try:
        # Test 1: Indexing
        entities, relationships = test_indexing_with_ast()
        
        # Test 2: Database storage
        conn, repo = test_database_storage(entities, relationships)
        
        # Test 3: Graph queries
        test_graph_queries(conn, repo)
        
        print("\n" + "=" * 80)
        print("✅ All Tests Complete!")
        print("=" * 80)
        print(f"\nDatabase saved at: {Path.home() / '.turingmind' / 'test_graph.db'}")
        print("You can query it using SQLite or the MCP tools.")
        
        conn.close()
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
