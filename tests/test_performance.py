"""
Performance Tests

Tests for performance and scalability.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from turingmind_mcp.config_manager import ConfigManager


class TestPerformance:
    """Test performance characteristics."""

    def test_config_read_write_performance(self):
        """Test config read/write performance."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Create large config
            large_config = {
                "mcpServers": {
                    f"server_{i}": {
                        "command": f"server-{i}",
                        "args": [str(j) for j in range(10)],
                        "env": {f"KEY_{j}": f"VALUE_{j}" for j in range(10)},
                    }
                    for i in range(100)
                }
            }

            # Measure write time
            start = time.time()
            manager.write_config(config_path, large_config)
            write_time = time.time() - start

            # Measure read time
            start = time.time()
            read_config = manager.read_config(config_path)
            read_time = time.time() - start

            # Should complete in reasonable time (< 1 second)
            assert write_time < 1.0
            assert read_time < 1.0

            # Verify data integrity
            assert len(read_config["mcpServers"]) == 100

    def test_config_validation_performance(self):
        """Test config validation performance."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Create config with many servers
            config = {
                "mcpServers": {
                    f"server_{i}": {
                        "command": f"server-{i}",
                        "args": [],
                        "env": {},
                    }
                    for i in range(50)
                }
            }

            manager.write_config(config_path, config)

            # Measure validation time
            start = time.time()
            is_valid, errors = manager.validate_config(config_path)
            validation_time = time.time() - start

            # Should complete quickly (< 0.5 seconds)
            assert validation_time < 0.5
            assert is_valid is True

    def test_multiple_config_operations(self):
        """Test multiple config operations in sequence."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Perform multiple operations
            operations = []
            for i in range(20):
                start = time.time()
                manager.add_mcp_server(
                    config_path=config_path,
                    server_name=f"server_{i}",
                    command=f"cmd-{i}",
                )
                operations.append(time.time() - start)

            # All operations should be fast
            assert all(op < 0.1 for op in operations)
            assert max(operations) < 0.1  # Even worst case should be fast
