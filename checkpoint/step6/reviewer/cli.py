"""Command-line entry point for the deterministic Step 6 reviewer."""
from __future__ import annotations
import argparse
from pathlib import Path
from .loader import load_bundle
from .report import write_report
from .semantics import review_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Review one Shodh workflow bundle")
    parser.add_argument("bundle", help="path to a bundle directory")
    parser.add_argument("--out", default="reports/step6-report.json", help="JSON report path")
    args = parser.parse_args()
    report = review_bundle(load_bundle(args.bundle))
    path = write_report(report, args.out)
    print(f"Status: {report.status}")
    print(report.summary)
    print(f"Findings: {len(report.findings)} | Coverage entries: {len(report.coverage.entries)}")
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
