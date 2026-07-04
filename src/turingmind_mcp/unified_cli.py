#!/usr/bin/env python3
"""
Unified CLI Tool for TuringMind-MCP

Provides single command interface for all platform setup and management.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config_manager import ConfigManager
from .errors import ConfigError, ConnectionError, handle_error

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("turingmind-cli")


def setup_platform(platform: str, project_root: Path | None = None) -> int:
    """
    Setup TuringMind-MCP for a specific platform.
    
    Args:
        platform: Platform name (claude_desktop, claude_cli, cursor)
        project_root: Project root directory (for project-based configs)
        
    Returns:
        0 on success, 1 on error
    """
    try:
        config_manager = ConfigManager(project_root)

        if platform == "claude_desktop":
            config_path = config_manager.get_claude_desktop_config_path()
        elif platform == "claude_cli":
            config_path = config_manager.get_claude_cli_config_path()
        elif platform == "cursor":
            config_path = config_manager.get_cursor_config_path()
        else:
            print(f"❌ Unknown platform: {platform}")
            print("Supported platforms: claude_desktop, claude_cli, cursor")
            return 1

        # Check if turingmind-mcp is installed
        import shutil

        if not shutil.which("turingmind-mcp"):
            print("⚠️  turingmind-mcp not found in PATH")
            response = input("Install turingmind-mcp? (y/N): ")
            if response.lower() == "y":
                import subprocess

                subprocess.run([sys.executable, "-m", "pip", "install", "turingmind-mcp"])
                print("✅ Installed turingmind-mcp")
            else:
                print("❌ turingmind-mcp is required")
                return 1

        # Add MCP server
        success = config_manager.add_mcp_server(
            config_path=config_path,
            server_name="turingmind",
            command="turingmind-mcp",
            env={"TURINGMIND_API_URL": "https://api.turingmind.ai"},
        )

        if success:
            print(f"✅ Configuration added to {config_path}")
        else:
            print(f"⚠️  Server already exists in {config_path}")
            response = input("Update existing configuration? (y/N): ")
            if response.lower() == "y":
                config_manager.update_mcp_server(
                    config_path=config_path,
                    server_name="turingmind",
                    command="turingmind-mcp",
                    env={"TURINGMIND_API_URL": "https://api.turingmind.ai"},
                )
                print(f"✅ Configuration updated in {config_path}")
            else:
                print("Skipped")

        # Platform-specific next steps
        print("\n📋 Next steps:")
        if platform == "claude_desktop":
            print("1. Restart Claude Desktop completely")
            print("2. In Claude Desktop, say: 'Log me into TuringMind'")
        elif platform == "claude_cli":
            print("1. Verify: claude mcp")
            print("2. Test: claude -p 'Review my code' --allowedTools 'turingmind_*'")
        elif platform == "cursor":
            print("1. Restart Cursor IDE")
            print("2. Verify: Settings → Tools & Integrations → MCP")
            print("3. In Cursor chat: 'Log me into TuringMind'")
            print("4. Plugin hooks: ensure API server on :8477 (see `turingmind install-api-daemon`)")

        if platform == "cursor":
            from .daemon_setup import offer_cursor_daemon_install

            offer_cursor_daemon_install()

        return 0

    except Exception as e:
        print(f"❌ Error: {handle_error(e, f'setup {platform}')}")
        return 1


def validate_config(platform: str, project_root: Path | None = None) -> int:
    """
    Validate configuration for a platform.
    
    Args:
        platform: Platform name
        project_root: Project root directory
        
    Returns:
        0 if valid, 1 if invalid
    """
    try:
        config_manager = ConfigManager(project_root)

        if platform == "claude_desktop":
            config_path = config_manager.get_claude_desktop_config_path()
        elif platform == "claude_cli":
            config_path = config_manager.get_claude_cli_config_path()
        elif platform == "cursor":
            config_path = config_manager.get_cursor_config_path()
        else:
            print(f"❌ Unknown platform: {platform}")
            return 1

        is_valid, errors = config_manager.validate_config(config_path)

        if is_valid:
            print(f"✅ Configuration is valid: {config_path}")
            return 0
        else:
            print(f"❌ Configuration has errors: {config_path}")
            for error in errors:
                print(f"  - {error}")
            return 1

    except FileNotFoundError:
        print(f"❌ Config file not found")
        return 1
    except Exception as e:
        print(f"❌ Error: {handle_error(e)}")
        return 1


def diagnose() -> int:
    """
    Diagnose installation and configuration.
    
    Returns:
        0 on success, 1 on error
    """
    print("🔍 Diagnosing TuringMind-MCP installation...\n")

    # Check Python version
    import sys

    python_version = sys.version_info
    if python_version >= (3, 10):
        print(f"✅ Python version: {python_version.major}.{python_version.minor}")
    else:
        print(f"❌ Python version: {python_version.major}.{python_version.minor} (requires 3.10+)")
        return 1

    # Check turingmind-mcp installation
    import shutil

    if shutil.which("turingmind-mcp"):
        print("✅ turingmind-mcp found in PATH")
        # Test command
        import subprocess

        try:
            result = subprocess.run(
                ["turingmind-mcp", "--help"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                print("✅ turingmind-mcp command works")
            else:
                print("⚠️  turingmind-mcp command returned error")
        except Exception as e:
            print(f"⚠️  Failed to test turingmind-mcp: {e}")
    else:
        print("❌ turingmind-mcp not found in PATH")
        print("   Install: pip install turingmind-mcp")
        return 1

    # Check configurations
    print("\n📋 Checking configurations...")
    config_manager = ConfigManager()

    platforms = {
        "claude_desktop": config_manager.get_claude_desktop_config_path(),
        "claude_cli": config_manager.get_claude_cli_config_path(),
        "cursor": config_manager.get_cursor_config_path(),
    }

    for platform_name, config_path in platforms.items():
        if config_path.exists():
            is_valid, errors = config_manager.validate_config(config_path)
            if is_valid:
                config = config_manager.get_turingmind_config(platform_name)
                if config:
                    print(f"✅ {platform_name}: Configured")
                else:
                    print(f"⚠️  {platform_name}: Config exists but turingmind not found")
            else:
                print(f"❌ {platform_name}: Invalid config")
                for error in errors:
                    print(f"     - {error}")
        else:
            print(f"⚪ {platform_name}: Not configured")

    from .daemon_setup import diagnose_daemon

    print("\n🖥️  V2 API daemon (plugin hooks, port 8477)...")
    diagnose_daemon()

    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="TuringMind-MCP Unified CLI Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  turingmind setup claude_desktop    # Setup for Claude Desktop
  turingmind setup cursor            # Setup for Cursor IDE/CLI
  turingmind install-api-daemon      # macOS: background API server (launchd)
  turingmind validate claude_desktop # Validate configuration
  turingmind diagnose                 # Diagnose installation
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Setup TuringMind-MCP for a platform")
    setup_parser.add_argument(
        "platform",
        choices=["claude_desktop", "claude_cli", "cursor"],
        help="Platform to setup",
    )
    setup_parser.add_argument(
        "--project-root",
        type=Path,
        help="Project root directory (for project-based configs)",
    )

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    validate_parser.add_argument(
        "platform",
        choices=["claude_desktop", "claude_cli", "cursor"],
        help="Platform to validate",
    )
    validate_parser.add_argument(
        "--project-root",
        type=Path,
        help="Project root directory",
    )

    # Diagnose command
    subparsers.add_parser("diagnose", help="Diagnose installation and configuration")

    # Live memory effectiveness scorecard (Gate 2)
    assess_parser = subparsers.add_parser(
        "memory-assess",
        help="Assess live memory-engine layers (API :8477 + ~/.turingmind/memory.db)",
    )
    assess_parser.add_argument("--repo", help="Repository (owner/repo)")
    assess_parser.add_argument(
        "--api-url",
        help="V2 API base URL (default TURINGMIND_LOCAL_API_URL or http://127.0.0.1:8477)",
    )
    assess_parser.add_argument(
        "--workspace",
        type=Path,
        help="Git workspace for branch inference (default TURINGMIND_WORKSPACE_DIR)",
    )
    assess_parser.add_argument("--json", action="store_true", help="JSON output")
    assess_parser.add_argument(
        "--keep-probes",
        action="store_true",
        help="Leave probe memories in the database",
    )

    # macOS launchd daemon for V2 API (Cursor plugin hooks)
    daemon_parser = subparsers.add_parser(
        "install-api-daemon",
        help="Install V2 API server as launchd agent (macOS, port 8477)",
    )
    daemon_parser.add_argument(
        "action",
        nargs="?",
        choices=["install", "uninstall", "status"],
        default="install",
        help="install (default), uninstall, or status",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "setup":
        return setup_platform(args.platform, args.project_root)
    elif args.command == "validate":
        return validate_config(args.platform, args.project_root)
    elif args.command == "diagnose":
        return diagnose()
    elif args.command == "memory-assess":
        from .memory_effectiveness_assess import format_report, run_assessment

        try:
            report = run_assessment(
                repo=args.repo,
                api_url=args.api_url,
                workspace_dir=args.workspace,
                cleanup_probes=not args.keep_probes,
            )
        except (ValueError, ConnectionError) as exc:
            print(f"❌ {exc}")
            return 1
        print(format_report(report, as_json=args.json))
        return 1 if any(layer.status == "fail" for layer in report.layers) else 0
    elif args.command == "install-api-daemon":
        from .daemon_setup import install, status, uninstall

        action = args.action
        if action == "uninstall":
            return uninstall()
        if action == "status":
            return status()
        return install()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
