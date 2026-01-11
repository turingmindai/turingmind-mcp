# Changelog

All notable changes to the TuringMind MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-01-11

### Added
- `turingmind_initiate_login` tool - Start device code authentication flow
- `turingmind_poll_login` tool - Poll for login completion and auto-save API key
- `turingmind_submit_feedback` tool - Submit feedback on review issues (fixed, dismissed, false_positive)
- Automatic API key persistence to `~/.turingmind/config`
- Support for `TURINGMIND_API_URL` configuration

### Changed
- Login flow no longer requires pre-configured API key
- Improved error messages for authentication failures

## [0.1.0] - 2026-01-05

### Added
- Initial release
- `turingmind_validate_auth` tool - Validate API key and get account info
- `turingmind_upload_review` tool - Upload code review results to cloud
- `turingmind_get_context` tool - Fetch memory context for repositories
- Support for Claude Desktop integration
- Type-safe Pydantic models for all inputs
