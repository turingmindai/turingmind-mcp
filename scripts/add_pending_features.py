#!/usr/bin/env python3
"""
Add pending/proposed features to the Features Board database.

This script creates features for all the proposed items from the roadmap:
- Phase 2: Auto-Aggregation
- Phase 3: Stage 5 LLM Analysis
- Phase 4: Advanced Features
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

def get_repo_identifier():
    """Get repository identifier - detect from current workspace or use default."""
    # Try to get from environment first
    repo = os.environ.get('TURINGMIND_REPO')
    if repo:
        return repo
    
    # Try to detect from common workspace locations
    common_workspaces = [
        '/Users/turingmindai/Documents/VSCodeProjects/turingmind-vscode',
        '/Users/turingmindai/Documents/VSCodeProjects/Turingmind-App',
    ]
    
    for workspace_path in common_workspaces:
        if os.path.exists(workspace_path):
            folder_name = os.path.basename(workspace_path)
            return f'local/{folder_name}'
    
    # Default fallback
    return 'local/workspace'

def add_pending_features(db: MemoryDatabase, repo: str):
    """Add all pending features to the database."""
    
    features = [
        # Phase 2: Auto-Aggregation
        {
            'feature_name': 'Auto-Recalculate on Task Changes',
            'description': 'Automatically recalculate feature metrics when task phase or status changes. Triggers recalculation without manual intervention.',
            'priority': 'high',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Background Recalculation',
            'description': 'Periodic background recalculation of feature metrics (e.g., every 5 minutes) to ensure data is always up-to-date.',
            'priority': 'medium',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Real-time Feature Updates',
            'description': 'Live updates to Features Board when tasks change, without requiring manual refresh. WebSocket-based real-time synchronization.',
            'priority': 'high',
            'domain': 'backend',
            'status': 'backlog'
        },
        
        # Phase 3: Stage 5 LLM Analysis
        {
            'feature_name': 'Stage 5: Feature-Level LLM Analysis',
            'description': 'Strategic LLM analysis of features: generate feature summaries, identify dependencies, assess risks, determine release readiness, and provide recommendations. Analyzes all linked tasks to provide high-level insights.',
            'priority': 'high',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Feature Dependency Detection',
            'description': 'LLM-powered detection of feature dependencies. Identifies which features depend on others and which features are blocked by dependencies.',
            'priority': 'medium',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Feature Risk Assessment',
            'description': 'LLM analyzes linked tasks to assess overall feature risk level (low/medium/high) and identify key risks with mitigation strategies.',
            'priority': 'medium',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Release Readiness Analysis',
            'description': 'LLM calculates feature release readiness percentage, identifies blockers, and summarizes remaining work needed for completion.',
            'priority': 'high',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Feature Prioritization Suggestions',
            'description': 'LLM suggests feature priority adjustments based on task priorities, dependencies, business value, and technical complexity. Auto-apply suggestions with user confirmation.',
            'priority': 'low',
            'domain': 'backend',
            'status': 'backlog'
        },
        
        # Phase 4: Advanced Features
        {
            'feature_name': 'Feature Dependencies Visualization',
            'description': 'Visual graph/diagram showing feature relationships, dependencies, and blocking relationships. Interactive visualization of feature dependency network.',
            'priority': 'medium',
            'domain': 'frontend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Release Planning with LLM',
            'description': 'LLM-powered release planning: suggests which features should be grouped into releases based on dependencies, complexity, and business value.',
            'priority': 'medium',
            'domain': 'backend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Risk Assessment Dashboard',
            'description': 'Centralized dashboard showing risk levels across all features, high-risk features, and mitigation strategies. Visual risk heatmap.',
            'priority': 'low',
            'domain': 'frontend',
            'status': 'backlog'
        },
        {
            'feature_name': 'Feature Impact Analysis',
            'description': 'Analyze downstream effects of feature changes. Understand which features and tasks are impacted when a feature is modified or blocked.',
            'priority': 'low',
            'domain': 'backend',
            'status': 'backlog'
        },
    ]
    
    created_count = 0
    skipped_count = 0
    
    print(f"Adding {len(features)} pending features to repo: {repo}\n")
    
    for feature_data in features:
        try:
            # Check if feature already exists
            existing_features = db.get_features(repo)
            feature_exists = any(
                f['feature_name'] == feature_data['feature_name'] 
                for f in existing_features
            )
            
            if feature_exists:
                print(f"⏭️  Skipping (already exists): {feature_data['feature_name']}")
                skipped_count += 1
                continue
            
            # Create feature
            feature_id = db.create_feature(
                repo=repo,
                feature_name=feature_data['feature_name'],
                description=feature_data['description'],
                priority=feature_data['priority'],
                domain=feature_data['domain']
            )
            
            # Update status if not default
            if feature_data.get('status') and feature_data['status'] != 'backlog':
                db.update_feature(feature_id, {'status': feature_data['status']})
            
            print(f"✅ Created: {feature_data['feature_name']} ({feature_id})")
            created_count += 1
            
        except Exception as e:
            print(f"❌ Error creating '{feature_data['feature_name']}': {e}")
    
    print(f"\n📊 Summary:")
    print(f"   Created: {created_count}")
    print(f"   Skipped: {skipped_count}")
    print(f"   Total: {len(features)}")

def main():
    """Main entry point."""
    repo = get_repo_identifier()
    
    # Initialize database
    db_path = os.environ.get('TURINGMIND_DB_PATH')
    if not db_path:
        # Default to ~/.turingmind/memory.db
        home = Path.home()
        db_path = home / '.turingmind' / 'memory.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"📁 Database: {db_path}")
    print(f"📦 Repository: {repo}\n")
    
    db = MemoryDatabase(str(db_path))
    
    try:
        add_pending_features(db, repo)
        print("\n✅ Done!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
