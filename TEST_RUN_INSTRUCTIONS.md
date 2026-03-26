# Test Run Instructions

## Running Tests

### Install Dependencies

```bash
cd turingmind-mcp
pip install -e .[dev]  # Install with dev dependencies
# OR
pip install pytest pytest-asyncio pytest-mock
```

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test Files

```bash
# Test cursor database reader
pytest tests/test_cursor_database_reader.py -v

# Test chat capture logic
pytest tests/test_chat_capture.py -v

# Test database state management
pytest tests/test_database_chat_state.py -v

# Test bridge server integration
pytest tests/test_bridge_server_chat_capture.py -v

# Test integration (end-to-end)
pytest tests/test_integration_chat_capture.py -v
```

### Run Specific Test Classes

```bash
pytest tests/test_cursor_database_reader.py::TestExtractMetadata -v
```

### Run Specific Tests

```bash
pytest tests/test_chat_capture.py::TestCheckExchanges::test_check_exchanges_ready_to_capture -v
```

### Run with Coverage

```bash
pytest tests/ --cov=turingmind_mcp --cov-report=html
```

## Test Structure

### Unit Tests
- `test_cursor_database_reader.py` - Database access functions
- `test_chat_capture.py` - Chat capture logic
- `test_database_chat_state.py` - State management

### Integration Tests
- `test_bridge_server_chat_capture.py` - Bridge server handlers
- `test_integration_chat_capture.py` - End-to-end flows

## Expected Test Results

### ✅ Should Pass (After Implementation)
- All tests in `test_cursor_database_reader.py`
- All tests in `test_chat_capture.py`
- All tests in `test_database_chat_state.py`
- All tests in `test_bridge_server_chat_capture.py`
- All tests in `test_integration_chat_capture.py`

### ⚠️ May Need Mocking
- Tests that require actual Cursor database (will be skipped if not found)
- Tests that require git repository (will be skipped if not in git repo)

## Test Fixtures

Tests use pytest fixtures for:
- Temporary directories (`tmp_path`)
- Mock databases
- Mock WebSocket connections
- Test Cursor database setup

## Notes

- Tests use temporary databases (no real data modified)
- Git operations are mocked in most tests
- Real Cursor database tests require actual database (optional)
