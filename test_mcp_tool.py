#!/usr/bin/env python3
"""Test script for turingmind_enhance_chat_analysis MCP tool."""

import sys
import json
import asyncio
from pathlib import Path

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

async def test_mcp_tool():
    """Test the turingmind_enhance_chat_analysis MCP tool."""
    from turingmind_mcp.server import call_tool
    
    # Sample test data
    test_data = {
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
    
    print("\n🧪 Testing turingmind_enhance_chat_analysis MCP tool...")
    print(f"   Input: {len(test_data['user_prompts'])} prompts, {len(test_data['assistant_responses'])} responses")
    
    try:
        result = await call_tool("turingmind_enhance_chat_analysis", test_data)
        
        if result and len(result) > 0:
            response_text = result[0].text
            print("\n✅ Tool call successful!")
            
            # Try to parse JSON response
            try:
                response_json = json.loads(response_text)
                print(f"\n📊 Response structure:")
                print(f"   - summary: {response_json.get('summary', 'N/A')[:100]}...")
                print(f"   - threadName: {response_json.get('threadName', 'N/A')}")
                print(f"   - keyDecisions: {len(response_json.get('keyDecisions', []))} items")
                print(f"   - actionItems: {len(response_json.get('actionItems', []))} items")
                return True
            except json.JSONDecodeError:
                print(f"\n⚠️  Response is not JSON (might be error message):")
                print(response_text[:500])
                return False
        else:
            print("\n❌ Tool returned empty result")
            return False
            
    except Exception as e:
        print(f"\n❌ Tool call failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_mcp_tool())
    sys.exit(0 if success else 1)
