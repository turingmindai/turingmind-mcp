"""
TuringMind v2 Engine — Security Scanner (Phase 2.5a)

Wraps the OpenGrep CLI to perform incremental OWASP security scanning
on changed files and inject findings into the Decision Queue as gaps.

Design principles:
- Incremental: only scan files changed since last commit (git diff)
- Deduplicated: hash findings to prevent duplicate gap injection
- Observable: parse errors bubble as SECURITY_BLINDSPOT gaps
- Thin daemon: all intelligence lives here, daemon makes one curl call
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("turingmind_security")


@dataclass
class Finding:
    """A single OpenGrep scan finding."""
    rule_id: str
    file_path: str
    line_start: int
    line_end: int
    message: str
    severity: str  # ERROR, WARNING, INFO
    matched_code: str = ""

    @property
    def dedup_hash(self) -> str:
        """Deterministic hash for deduplication across scan cycles."""
        key = f"{self.file_path}:{self.line_start}:{self.rule_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class ParseError:
    """An OpenGrep file parse error — a security blindspot."""
    file_path: str
    language: str
    error_message: str


@dataclass
class ScanResult:
    """The complete output of an OpenGrep scan cycle."""
    findings: list[Finding] = field(default_factory=list)
    parse_errors: list[ParseError] = field(default_factory=list)
    files_scanned: int = 0
    scan_ok: bool = True
    error_message: str = ""


@dataclass
class CycleResult:
    """The result of a full security cycle (scan + gap injection)."""
    findings_total: int = 0
    findings_new: int = 0
    findings_duplicate: int = 0
    blindspots: int = 0
    gaps_injected: list[dict] = field(default_factory=list)
    scan_ok: bool = True
    error_message: str = ""


class SecurityScanner:
    """Wraps the OpenGrep CLI and manages the scan → gap injection lifecycle."""

    def __init__(self, workspace_dir: str, rules_dir: Optional[str] = None):
        self.workspace_dir = Path(workspace_dir)
        self.rules_dir = Path(rules_dir) if rules_dir else self.workspace_dir / ".opengrep" / "rules"
        # Track previously injected finding hashes to prevent duplicates
        self._injected_hashes: set[str] = set()

    # ── Public API ──────────────────────────────────────────────────────

    def get_changed_files(self, base_ref: str = "HEAD~1") -> list[str]:
        """Get list of files changed since `base_ref` via git diff.
        
        Falls back to listing tracked files if the repo has no commits yet
        or if git diff fails.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR", base_ref],
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                files = result.stdout.strip().split("\n")
                # Filter to only source code files (not configs, images, etc.)
                return [f for f in files if self._is_scannable(f)]
            
            # Fallback: try HEAD vs working tree for uncommitted changes
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                files = result.stdout.strip().split("\n")
                return [f for f in files if self._is_scannable(f)]

            return []
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"git diff failed: {e}")
            return []

    def run_scan(self, target_files: list[str]) -> ScanResult:
        """Run opengrep scan against specific files using BOTH community rules
        (--config auto) AND custom rules from .opengrep/rules/.
        
        Returns parsed findings and any parse errors.
        """
        if not target_files:
            return ScanResult(files_scanned=0)

        if not self._opengrep_available():
            return ScanResult(
                scan_ok=False,
                error_message="opengrep binary not found in PATH. Install with: curl -fsSL https://get.opengrep.dev | bash"
            )

        # Build absolute paths for target files
        abs_targets = []
        for f in target_files:
            p = Path(f)
            if not p.is_absolute():
                p = self.workspace_dir / f
            if p.exists():
                abs_targets.append(str(p))

        if not abs_targets:
            return ScanResult(files_scanned=0)

        # Build config flags: always use community rules + custom rules if they exist
        config_args = ["--config", "auto"]
        rules_path = str(self.rules_dir)
        if Path(rules_path).exists() and list(Path(rules_path).glob("*.yml")):
            config_args.extend(["--config", rules_path])

        try:
            cmd = [
                "opengrep", "scan",
                "--json",
                *config_args,
                *abs_targets,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,  # 3 min for larger community ruleset
                cwd=str(self.workspace_dir),
            )
            return self._parse_opengrep_output(result.stdout, result.stderr, len(abs_targets))

        except subprocess.TimeoutExpired:
            return ScanResult(
                scan_ok=False,
                error_message="opengrep scan timed out after 180 seconds"
            )
        except Exception as e:
            return ScanResult(
                scan_ok=False,
                error_message=f"opengrep scan failed: {e}"
            )

    def run_security_cycle(self, repo: str) -> CycleResult:
        """The single entry point called by POST /api/v2/security/cycle.
        
        1. Get changed files via git diff
        2. Run opengrep scan on changed files
        3. Parse errors → inject SECURITY_BLINDSPOT gaps
        4. Hash findings for deduplication
        5. Inject new findings as Decision Queue gaps
        
        Returns a CycleResult with counts and injected gaps.
        """
        cycle = CycleResult()

        # 1. Get changed files
        changed_files = self.get_changed_files()
        if not changed_files:
            logger.info("No changed files to scan.")
            return cycle

        logger.info("Security scan: %d changed file(s) to scan", len(changed_files))

        # 2. Run scan
        scan = self.run_scan(changed_files)
        if not scan.scan_ok:
            cycle.scan_ok = False
            cycle.error_message = scan.error_message
            logger.error("Security scan failed: %s", scan.error_message)
            return cycle

        cycle.findings_total = len(scan.findings)

        # 3. Parse errors → blindspot gaps
        for pe in scan.parse_errors:
            gap = {
                "gap_type": "security_blindspot",
                "severity": "high",
                "node_id": f"BLINDSPOT::{pe.file_path}",
                "node_title": f"Security scanning blindspot: {pe.file_path}",
                "action": (
                    f"OpenGrep cannot parse '{pe.file_path}' ({pe.language}): {pe.error_message}. "
                    f"This file is NOT being scanned for vulnerabilities. "
                    f"Investigate whether the file uses unsupported syntax or needs a language override."
                ),
            }
            cycle.gaps_injected.append(gap)
            cycle.blindspots += 1

        # 4-5. Deduplicate and inject findings
        for finding in scan.findings:
            h = finding.dedup_hash
            if h in self._injected_hashes:
                cycle.findings_duplicate += 1
                continue

            self._injected_hashes.add(h)
            cycle.findings_new += 1

            severity = "critical" if finding.severity == "ERROR" else "high" if finding.severity == "WARNING" else "medium"
            gap = {
                "gap_type": "security_rule_violation",
                "severity": severity,
                "node_id": f"SECURITY::{finding.file_path}::{finding.rule_id}",
                "node_title": f"OWASP Violation: {finding.rule_id}",
                "file_path": finding.file_path,
                "line_start": finding.line_start,
                "line_end": finding.line_end,
                "matched_code": finding.matched_code,
                "action": (
                    f"OpenGrep rule '{finding.rule_id}' matched in '{finding.file_path}' "
                    f"at line {finding.line_start}: {finding.message}. "
                    f"Read the file, fix the vulnerability, and verify the fix resolves the finding."
                ),
            }
            cycle.gaps_injected.append(gap)

        logger.info(
            "Security cycle complete: %d findings (%d new, %d duplicate), %d blindspots",
            cycle.findings_total, cycle.findings_new, cycle.findings_duplicate, cycle.blindspots,
        )
        return cycle

    # ── Private ──────────────────────────────────────────────────────────

    def _opengrep_available(self) -> bool:
        """Check if the opengrep binary is installed and accessible."""
        try:
            result = subprocess.run(
                ["opengrep", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _parse_opengrep_output(self, stdout: str, stderr: str, file_count: int) -> ScanResult:
        """Parse the JSON output from opengrep scan --json.
        
        OpenGrep's --json flag outputs UI chrome (box drawings, status text)
        followed by a JSON block. We need to extract just the JSON portion.
        
        The JSON follows the Semgrep schema:
        {
            "version": "...",
            "results": [...findings...],
            "errors": [...parse errors...]
        }
        """
        result = ScanResult(files_scanned=file_count)

        if not stdout.strip():
            return result

        # Extract JSON block from mixed output
        # OpenGrep prints UI text then the JSON object starting with {"version"
        json_str = stdout
        json_start = stdout.find('{"version"')
        if json_start != -1:
            # Find the end of the JSON block (followed by blank line or EOF)
            remaining = stdout[json_start:]
            # The JSON is a single line, terminated by newline
            json_end = remaining.find('\n\n')
            if json_end != -1:
                json_str = remaining[:json_end].strip()
            else:
                json_str = remaining.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            if stderr.strip():
                logger.warning("OpenGrep non-JSON output. stderr: %s", stderr[:500])
            result.error_message = f"Failed to parse opengrep JSON output"
            result.scan_ok = False
            return result

        # Parse findings
        for item in data.get("results", []):
            finding = Finding(
                rule_id=item.get("check_id", "unknown"),
                file_path=item.get("path", ""),
                line_start=item.get("start", {}).get("line", 0),
                line_end=item.get("end", {}).get("line", 0),
                message=item.get("extra", {}).get("message", ""),
                severity=item.get("extra", {}).get("severity", "WARNING"),
                matched_code=item.get("extra", {}).get("lines", ""),
            )
            result.findings.append(finding)

        # Parse errors (security blindspots)
        for err in data.get("errors", []):
            pe = ParseError(
                file_path=err.get("path", err.get("long_msg", "")),
                language=err.get("language", "unknown"),
                error_message=err.get("short_msg", err.get("long_msg", "parse error")),
            )
            result.parse_errors.append(pe)

        return result

    @staticmethod
    def _is_scannable(filepath: str) -> bool:
        """Return True if the file is a source code file worth scanning."""
        scannable_extensions = {
            ".py", ".js", ".ts", ".tsx", ".jsx",
            ".java", ".go", ".rb", ".rs",
            ".yaml", ".yml",
            ".tf", ".hcl",
            ".sh", ".bash",
            ".dockerfile",
        }
        ext = Path(filepath).suffix.lower()
        name = Path(filepath).name.lower()

        # Also catch Dockerfiles (no extension)
        if name == "dockerfile" or name.startswith("dockerfile."):
            return True

        return ext in scannable_extensions
