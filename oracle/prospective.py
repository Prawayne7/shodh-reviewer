import argparse
import hashlib
import json
from pathlib import Path

from oracle.freeze import ROOT, current, verify_original


def snapshot():
    files = [
        *(ROOT / "reviewer").glob("*.py"),
        *(ROOT / "checkpoint/step6").rglob("*.py"),
        *(ROOT / "oracle").glob("*.py"),
        *(ROOT / "eval").glob("*.py"),
        ROOT / "requirements.txt",
        ROOT / "labels/heldout_manifest.json",
    ]
    return {
        "heldout_artifacts": current(),
        "source_sha256": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)
        },
    }


def verify():
    verify_original()
    path = ROOT / "labels/heldout_manifest_prospective.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["snapshot"] != snapshot():
        raise ValueError("Held-out artifacts or prospective source snapshot changed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.record:
        verify_original()
        path = ROOT / "labels/heldout_manifest_prospective.json"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({
                "scope": "Prospective rerun of the supplied split. Historical timing and tuning isolation are unverified.",
                "snapshot": snapshot(),
            }, stream, indent=2)
            stream.write("\n")
    verify()
    print(f"OK: original and prospective hashes verified for {len(current()['heldout'])} held-out bundles")


if __name__ == "__main__":
    main()
