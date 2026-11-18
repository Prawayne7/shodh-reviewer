"""Command-line entry point for the deterministic + advisory reviewer."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .loader import load_bundle
from .report import write_report
from .semantics import review_bundle
from .model_review import FakeModelClient, run_model_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review one Shodh workflow bundle")
    parser.add_argument("bundle", help="path to a bundle directory")
    parser.add_argument("--out", default="reports/review.json", help="JSON report path")
    parser.add_argument("--model", choices=("off", "fake"), default="fake",
                        help="advisory model mode; live provider is intentionally not configured")
    parser.add_argument("--fake-response", help="response payload to use on each fake attempt; __TIMEOUT__ and __ERROR__ are supported")
    args = parser.parse_args()
    bundle = load_bundle(args.bundle)
    report = review_bundle(bundle)
    if args.model == "fake":
        raw = Path(args.fake_response).read_text(encoding="utf-8").strip() if args.fake_response else '{"observations":[]}'
        run_model_review(bundle, report, FakeModelClient(responses=[raw, raw]))
    path = write_report(report, args.out)
    print(f"Status: {report.status}")
    print(report.summary)
    print(f"Findings: {len(report.findings)} | Coverage entries: {len(report.coverage.entries)}")
    print(f"Model calls: {len(report.provenance.model_calls)}")
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
