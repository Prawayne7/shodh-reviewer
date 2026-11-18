"""Run the real CLI locally and display its three verdicts."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from reviewer.schemas import Report


def main():
    with TemporaryDirectory() as directory:
        temp = Path(directory)
        timeout = temp / "timeout.txt"
        timeout.write_text("__TIMEOUT__", encoding="utf-8")
        for case, expected in (("b01", "PASS"), ("b14", "CHANGES_REQUIRED"), ("b19", "INCOMPLETE")):
            output = temp / (case + ".json")
            args = ["-m", "reviewer.cli", f"fixtures/bundles/{case}", "--out", str(output), "--model", "fake"]
            if case == "b19":
                args += ["--fake-response", str(timeout)]
            print("\n> python " + subprocess.list2cmdline(args), flush=True)
            subprocess.run([sys.executable, "-X", "utf8", *args], check=True)
            report = Report.model_validate_json(output.read_bytes())
            if report.status != expected:
                raise SystemExit(f"{case}: expected {expected}, got {report.status}")
            for finding in report.findings:
                ce = finding.counterexample
                print(f"  {finding.artifact}@{finding.version}#{finding.location} | source {finding.rule_ref}")
                if ce:
                    print(f"  {ce.ticket.wait_minutes} min: expected {ce.expected_route}, got {ce.actual_route}")
                    print(f"  Actual route selected by {ce.actual_by.location}")
            print("  Model outcomes:", ", ".join(call.outcome for call in report.provenance.model_calls))
            Path("reports/demo").mkdir(exist_ok=True)
            Path(f"reports/demo/{case}.json").write_bytes(output.read_bytes())


if __name__ == "__main__":
    main()
