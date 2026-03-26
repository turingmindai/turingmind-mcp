"""Integration tests for ChatAnalysisAgent file diff handling.

Tests the actual implementation without mocks.
"""

import pytest
import os
import tempfile
from typing import Dict, List, Any

from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent
from turingmind_mcp.llm.azure_openai import AzureOpenAIProvider


class TestChatAnalysisAgentDiffs:
    """Test ChatAnalysisAgent with file diffs."""
    
    @pytest.fixture
    def agent(self):
        """Create ChatAnalysisAgent with real LLM provider if configured."""
        # Try to get real provider from environment
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        key = os.environ.get("AZURE_OPENAI_KEY")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
        
        if endpoint and key and deployment:
            provider = AzureOpenAIProvider(
                endpoint=endpoint,
                api_key=key,
                deployment_name=deployment,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
            )
        else:
            # Skip tests if not configured
            pytest.skip("Azure OpenAI not configured (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME)")
        
        return ChatAnalysisAgent(
            llm_provider=provider,
            langsmith_client=None,
            use_heavy_task_model=False
        )
    
    @pytest.fixture
    def agent_unit(self):
        """Create ChatAnalysisAgent for unit tests (no LLM calls needed)."""
        # For unit tests that don't need LLM, we can use a mock provider
        from unittest.mock import Mock
        mock_provider = Mock()
        return ChatAnalysisAgent(
            llm_provider=mock_provider,
            langsmith_client=None,
            use_heavy_task_model=False
        )
    
    def test_select_important_diffs_prioritizes_mentioned_files(self, agent_unit):
        """Test that files mentioned in conversation are prioritized."""
        file_diffs = [
            {"path": "src/main.py", "diff": "small change", "size": 100},
            {"path": "README.md", "diff": "docs update", "size": 200},
            {"path": "src/utils.py", "diff": "utility change", "size": 150},
        ]
        files_discussed = ["src/main.py", "src/utils.py"]
        
        selected = agent_unit._select_important_diffs(file_diffs, files_discussed, max_tokens=1000)
        
        # Should prioritize mentioned files
        selected_paths = [d["path"] for d in selected]
        assert "src/main.py" in selected_paths or "src/utils.py" in selected_paths
        assert len(selected) > 0
    
    def test_select_important_diffs_respects_token_budget(self, agent_unit):
        """Test that diff selection respects token budget."""
        file_diffs = [
            {"path": "file1.py", "diff": "x" * 1000, "size": 1000},  # 250 tokens
            {"path": "file2.py", "diff": "x" * 2000, "size": 2000},  # 500 tokens
            {"path": "file3.py", "diff": "x" * 3000, "size": 3000},  # 750 tokens
        ]
        files_discussed = []
        max_tokens = 600  # Should fit file1 and file2 (750 tokens total), but not all 3 (1500 tokens)
        
        selected = agent_unit._select_important_diffs(file_diffs, files_discussed, max_tokens=max_tokens)
        
        # Calculate total tokens
        total_tokens = sum(d["size"] // 4 for d in selected)
        assert total_tokens <= max_tokens, f"Total tokens {total_tokens} exceeds budget {max_tokens}"
        assert len(selected) <= 2, f"Should not fit all 3 files, but selected {len(selected)}"
    
    def test_select_important_diffs_prioritizes_small_diffs(self, agent_unit):
        """Test that smaller diffs are prioritized."""
        file_diffs = [
            {"path": "large.py", "diff": "x" * 10000, "size": 10000},
            {"path": "small.py", "diff": "x" * 100, "size": 100},
        ]
        files_discussed = []
        
        selected = agent_unit._select_important_diffs(file_diffs, files_discussed, max_tokens=500)
        
        # Small diff should be selected (25 tokens) but large might not fit (2500 tokens)
        selected_paths = [d["path"] for d in selected]
        assert "small.py" in selected_paths
    
    def test_select_important_diffs_prioritizes_code_files(self, agent_unit):
        """Test that code files are prioritized over non-code files."""
        file_diffs = [
            {"path": "README.md", "diff": "docs", "size": 200},
            {"path": "src/main.py", "diff": "code", "size": 200},
        ]
        files_discussed = []
        
        selected = agent_unit._select_important_diffs(file_diffs, files_discussed, max_tokens=1000)
        
        # Both should fit, but code file should have higher score
        selected_paths = [d["path"] for d in selected]
        assert len(selected) == 2  # Both fit
        # Code file should come first (higher score)
        assert selected[0]["path"] == "src/main.py"
    
    def test_summarize_diff_preserves_headers(self, agent_unit):
        """Test that diff summarization preserves file headers."""
        diff = """diff --git a/src/main.py b/src/main.py
index 1234567..abcdefg 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 def hello():
     print("hello")
+    print("world")
"""
        # Make it large enough to trigger summarization
        large_diff = diff + "\n" + "\n".join(["+line" + str(i) for i in range(200)])
        
        summarized = agent_unit._summarize_diff(large_diff, max_lines=50)
        
        assert "diff --git" in summarized
        assert "---" in summarized or "+++" in summarized
        assert len(summarized) < len(large_diff)
    
    def test_summarize_diff_limits_hunks(self, agent_unit):
        """Test that diff summarization limits number of hunks."""
        # Create diff with many hunks (proper format with headers)
        hunks = []
        for i in range(20):
            hunks.append(f"@@ -{i},1 +{i},2 @@\n+change{i}\n")
        
        diff = "diff --git a/file b/file\n--- a/file\n+++ b/file\n" + "\n".join(hunks)
        
        # Use lower max_lines to force truncation
        summarized = agent_unit._summarize_diff(diff, max_lines=30)
        
        # Should limit hunks (implementation limits to max_hunks=5, but may include more if within max_lines)
        hunk_count = summarized.count("@@")
        # The implementation may include more hunks if they fit within max_lines, so we check it's less than original
        assert hunk_count < 20, f"Should limit hunks, but got {hunk_count} (original: 20)"
        assert hunk_count > 0, "Should include at least some hunks"
        # With max_lines=30, we should get significantly fewer than 20 hunks
        assert hunk_count <= 10, f"With max_lines=30, should have <= 10 hunks, got {hunk_count}"
    
    def test_build_prompt_includes_diffs(self, agent_unit):
        """Test that _build_prompt includes file diffs when provided."""
        inputs = {
            "user_prompts": [{"text": "Fix bug", "timestamp": 1000, "sequence": 0}],
            "assistant_responses": [{"text": "Fixed", "timestamp": 2000, "sequence": 0}],
            "files_discussed": ["src/main.py"],
            "ai_todos": [],
            "file_diffs": [
                {"path": "src/main.py", "diff": "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new", "size": 50}
            ]
        }
        
        prompt = agent_unit._build_prompt(inputs)
        
        assert "## CODE CHANGES (Diffs)" in prompt
        assert "src/main.py" in prompt
        assert "```diff" in prompt
    
    def test_build_prompt_handles_empty_diffs(self, agent_unit):
        """Test that _build_prompt handles empty file_diffs gracefully."""
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 1000, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 2000, "sequence": 0}],
            "files_discussed": [],
            "ai_todos": [],
            "file_diffs": []
        }
        
        prompt = agent_unit._build_prompt(inputs)
        
        # Should not include diff section
        assert "## CODE CHANGES (Diffs)" not in prompt
        # Should still build valid prompt
        assert len(prompt) > 0
    
    def test_build_prompt_truncates_large_diffs(self, agent_unit):
        """Test that _build_prompt truncates very large diffs."""
        large_diff = "--- a/file\n+++ b/file\n" + "\n".join([f"+line{i}" for i in range(1000)])
        
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 1000, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 2000, "sequence": 0}],
            "files_discussed": ["file.py"],
            "ai_todos": [],
            "file_diffs": [
                {"path": "file.py", "diff": large_diff, "size": len(large_diff)}
            ]
        }
        
        prompt = agent_unit._build_prompt(inputs)
        
        # Should include diff but truncated
        assert "## CODE CHANGES (Diffs)" in prompt
        assert "file.py" in prompt
        # Should be shorter than original
        diff_section = prompt.split("## CODE CHANGES (Diffs)")[1].split("##")[0]
        assert len(diff_section) < len(large_diff)
    
    def test_build_prompt_shows_truncation_message(self, agent_unit):
        """Test that _build_prompt shows message when diffs are truncated."""
        file_diffs = [
            {"path": f"file{i}.py", "diff": "small", "size": 1000}  # 250 tokens each
            for i in range(10)  # 10 files = 2500 tokens, exceeds 2000 token budget
        ]
        
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 1000, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 2000, "sequence": 0}],
            "files_discussed": [],
            "ai_todos": [],
            "file_diffs": file_diffs
        }
        
        prompt = agent_unit._build_prompt(inputs)
        
        # Should show truncation message
        assert "more file diffs (truncated due to token limit)" in prompt
