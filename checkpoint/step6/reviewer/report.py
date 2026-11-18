"""JSON serialization helpers for Step 6 reports."""
from pathlib import Path
from .schemas import Report


def write_report(report: Report, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target
