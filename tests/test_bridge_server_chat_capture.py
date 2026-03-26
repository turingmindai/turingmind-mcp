"""
Tests for Bridge Server Chat Capture Integration

Tests the bridge server handlers for chat capture.
"""

import pytest
import json
from unittest.mock import Mock, patch, AsyncMock, MagicMock

# Import the actual handle_message function
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turingmind_mcp.bridge_server import handle_message


class TestCheckExchangesHandler:
    """Tests for check_exchanges handler in bridge server"""
    
    @pytest.fixture
    def mock_websocket(self):
        """Mock WebSocket connection."""
        ws = Mock()
        ws.send = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.check_exchanges')
    @patch('turingmind_mcp.bridge_server.get_db')
    async def test_check_exchanges_handler_success(
        self,
        mock_get_db,
        mock_check_exchanges,
        mock_websocket
    ):
        """Test successful check_exchanges handler."""
        # Setup mocks
        mock_db = Mock()
        mock_get_db.return_value = mock_db
        
        mock_check_exchanges.return_value = {
            "exchanges": [
                {
                    "composerId": "test-composer",
                    "exchangeState": {"totalBubbles": 4},
                    "shouldEnhanceLLM": True,
                    "isUpdate": False
                }
            ]
        }
        
        # Create message
        message = json.dumps({
            "action": "check_exchanges",
            "request_id": "test-request-1",
            "repo": "test-repo",
            "session_start_time": 1000
        })
        
        await handle_message(mock_websocket, message)
        
        # Verify response was sent
        assert mock_websocket.send.called
        response = json.loads(mock_websocket.send.call_args[0][0])
        assert response["request_id"] == "test-request-1"
        assert "data" in response
        assert len(response["data"]["exchanges"]) == 1
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.check_exchanges')
    @patch('turingmind_mcp.bridge_server.get_db')
    async def test_check_exchanges_handler_no_exchanges(
        self,
        mock_get_db,
        mock_check_exchanges,
        mock_websocket
    ):
        """Test check_exchanges when no exchanges ready."""
        mock_db = Mock()
        mock_get_db.return_value = mock_db
        
        mock_check_exchanges.return_value = {"exchanges": []}
        
        message = json.dumps({
            "action": "check_exchanges",
            "request_id": "test-request-2",
            "repo": "test-repo"
        })
        
        await handle_message(mock_websocket, message)
        
        response = json.loads(mock_websocket.send.call_args[0][0])
        assert len(response["data"]["exchanges"]) == 0
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.check_exchanges')
    @patch('turingmind_mcp.bridge_server.get_db')
    async def test_check_exchanges_handler_error(
        self,
        mock_get_db,
        mock_check_exchanges,
        mock_websocket
    ):
        """Test check_exchanges handler error handling."""
        mock_db = Mock()
        mock_get_db.return_value = mock_db
        
        mock_check_exchanges.side_effect = Exception("Database error")
        
        message = json.dumps({
            "action": "check_exchanges",
            "request_id": "test-request-3",
            "repo": "test-repo"
        })
        
        await handle_message(mock_websocket, message)
        
        response = json.loads(mock_websocket.send.call_args[0][0])
        assert "error" in response
        assert "Database error" in response["error"]
    
    @pytest.mark.asyncio
    async def test_check_exchanges_handler_missing_repo(self, mock_websocket):
        """Test check_exchanges handler with missing repo parameter."""
        message = json.dumps({
            "action": "check_exchanges",
            "request_id": "test-request-4"
            # Missing repo
        })
        
        await handle_message(mock_websocket, message)
        
        # Should still work (repo defaults to 'local/workspace')
        assert mock_websocket.send.called
