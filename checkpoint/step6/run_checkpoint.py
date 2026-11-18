"""Run the preserved Step 6 deterministic reviewer against current frozen bundles.
Executed in a separate process so the checkpoint package cannot be shadowed by Step 9."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(HERE))
from reviewer.loader import load_bundle
from reviewer.semantics import review_bundle
labels_dir = ROOT / "labels"
rows = []
for label_path in sorted(labels_dir.glob("b*.yaml")):
    import yaml
    label = yaml.safe_load(label_path.read_text())
    bid = label_path.stem
    bundle = load_bundle(ROOT / "fixtures" / "bundles" / bid)
    report = review_bundle(bundle)
    rows.append({"bundle": bid, "split": label["split"], "case_type": label["case_type"],
                 "expected_status": label["expected_status"], "actual_status": report.status})
print(json.dumps(rows))
