#!/usr/bin/env python3
"""
Migrate features from one repo to another.
Useful when features were created with wrong repo identifier.
"""

import sys
import os
from pathlib import Path

# Add src to path
_this_file = Path(__file__)
_src_dir = _this_file.parent.parent / "src"
sys.path.insert(0, str(_src_dir))

# Import directly to avoid __init__.py dependencies
import importlib.util
database_path = _src_dir / "turingmind_mcp" / "database.py"
spec = importlib.util.spec_from_file_location("database", database_path)
database_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database_module)
MemoryDatabase = database_module.MemoryDatabase

def migrate_features(db, from_repo: str, to_repo: str):
    """Migrate all features from one repo to another."""
    features = db.get_features(from_repo)
    
    if not features:
        print(f"No features found in '{from_repo}'")
        return
    
    print(f"Found {len(features)} features in '{from_repo}'")
    print(f"Migrating to '{to_repo}'...\n")
    
    migrated = 0
    skipped = 0
    
    for feature in features:
        # Check if feature already exists in target repo
        existing = db.get_features(to_repo)
        exists = any(f['feature_name'] == feature['feature_name'] for f in existing)
        
        if exists:
            print(f"⏭️  Skipping (already exists): {feature['feature_name']}")
            skipped += 1
            continue
        
        # Update the repo for this feature
        try:
            db.update_feature(feature['feature_id'], {'repo': to_repo})
            print(f"✅ Migrated: {feature['feature_name']} ({feature['feature_id']})")
            migrated += 1
        except Exception as e:
            print(f"❌ Error migrating '{feature['feature_name']}': {e}")
    
    print(f"\n📊 Summary:")
    print(f"   Migrated: {migrated}")
    print(f"   Skipped: {skipped}")
    print(f"   Total: {len(features)}")

def main():
    """Main entry point."""
    from_repo = os.environ.get('FROM_REPO', 'local/workspace')
    to_repo = os.environ.get('TO_REPO', 'local/turingmind-vscode')
    
    # Initialize database
    db_path = os.environ.get('TURINGMIND_DB_PATH')
    if not db_path:
        home = Path.home()
        db_path = home / '.turingmind' / 'memory.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"📁 Database: {db_path}")
    print(f"📦 From: {from_repo}")
    print(f"📦 To: {to_repo}\n")
    
    db = MemoryDatabase(str(db_path))
    
    try:
        migrate_features(db, from_repo, to_repo)
        print("\n✅ Done!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
