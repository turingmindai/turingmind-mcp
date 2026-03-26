#!/usr/bin/env python3
"""Test ChatAnalysisAgent directly (without MCP server)."""

import sys
import json
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ Loaded .env from {env_path}")
else:
    print("⚠️  No .env file found")

async def test_agent_direct():
    """Test ChatAnalysisAgent directly."""
    from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent
    from turingmind_mcp.llm.config import get_llm_provider, get_langsmith_client
    
    print("\n🧪 Testing ChatAnalysisAgent directly...")
    
    # Get LLM provider
    llm_provider = get_llm_provider("azure")
    if not llm_provider:
        print("❌ LLM provider not configured. Skipping test.")
        return False
    
    print(f"✅ LLM Provider: {llm_provider.endpoint} / {llm_provider.deployment_name}")
    
    # Get LangSmith client
    langsmith_client = get_langsmith_client()
    if langsmith_client:
        print("✅ LangSmith client available")
    else:
        print("⚠️  LangSmith not configured (optional)")
    
    # Create agent
    agent = ChatAnalysisAgent(
        llm_provider=llm_provider,
        langsmith_client=langsmith_client,
        use_heavy_task_model=False
    )
    
    print("✅ ChatAnalysisAgent created")
    
    # Test data
    test_inputs = {
        "user_prompts": [
            {
                "text": "Fix the authentication bug in the login page",
                "timestamp": 1000000,
                "sequence": 0
            },
            {
                "text": "Actually, let's use OAuth instead",
                "timestamp": 2000000,
                "sequence": 1
            }
        ],
        "assistant_responses": [
            {
                "text": "I'll help you fix the authentication bug. Let me check the login code...",
                "timestamp": 1001000,
                "sequence": 0
            },
            {
                "text": "Good idea! OAuth is more secure. Let me implement that...",
                "timestamp": 2001000,
                "sequence": 1
            }
        ],
        "files_discussed": ["src/auth.py", "src/login.py"],
        "ai_todos": [
            {"content": "Implement OAuth flow", "status": "pending"},
            {"content": "Update tests", "status": "pending"}
        ],
        "reasoning": None,
        "previous_summary": None
    }
    
    print(f"\n📊 Test inputs:")
    print(f"   - {len(test_inputs['user_prompts'])} user prompts")
    print(f"   - {len(test_inputs['assistant_responses'])} assistant responses")
    print(f"   - {len(test_inputs['files_discussed'])} files discussed")
    print(f"   - {len(test_inputs['ai_todos'])} AI todos")
    
    try:
        print("\n🚀 Executing agent...")
        result = await agent.execute(
            inputs=test_inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis", "test"],
            extra_metadata={"test": True}
        )
        
        print("\n✅ Agent execution successful!")
        print(f"\n📋 Result:")
        print(f"   - threadName: {result.get('threadName', 'N/A')}")
        print(f"   - summary: {result.get('summary', 'N/A')[:100]}...")
        print(f"   - keyDecisions: {len(result.get('keyDecisions', []))} items")
        print(f"   - actionItems: {len(result.get('actionItems', []))} items")
        
        if result.get('keyDecisions'):
            print(f"\n   Key Decisions:")
            for i, decision in enumerate(result['keyDecisions'][:3], 1):
                print(f"     {i}. {decision[:80]}...")
        
        if result.get('actionItems'):
            print(f"\n   Action Items:")
            for i, item in enumerate(result['actionItems'][:3], 1):
                print(f"     {i}. [{item.get('priority', 'medium')}] {item.get('task', 'N/A')[:60]}...")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Agent execution failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_agent_direct())
    sys.exit(0 if success else 1)
