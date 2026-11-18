"""Centralised Step 6 status policy (kept separate for unit testing)."""
from .schemas import Coverage, Finding, CheckResult


def decide_status(findings: list[Finding], coverage: Coverage, checks: list[CheckResult]) -> str:
    """Return CHANGES_REQUIRED, INCOMPLETE or PASS in assessment precedence order."""
    if any(f.severity == "blocker" for f in findings):
        return "CHANGES_REQUIRED"
    if coverage.unreviewed() or any(c.required and c.status != "passed" for c in checks):
        return "INCOMPLETE"
    return "PASS"
