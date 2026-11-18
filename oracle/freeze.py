"""Verify the supplied held-out manifest without overwriting its history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "labels" / "heldout_manifest.json"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_dir():
        for p in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
            if p.is_file():
                h.update(p.relative_to(path).as_posix().encode())
                h.update(p.read_bytes())
    else:
        h.update(path.read_bytes())
    return h.hexdigest()


def current() -> dict:
    labels = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((ROOT / "labels").glob("b*.yaml"))}
    held = [b for b, lab in labels.items() if lab["split"] == "heldout"]
    return {
        "heldout": {
            b: {
                "label_sha256": sha(ROOT / "labels" / f"{b}.yaml"),
                "bundle_sha256": sha(ROOT / "fixtures" / "bundles" / b),
                "model_script_sha256": sha(ROOT / labels[b]["model_script"]) if labels[b]["model_script"] else None,
            }
            for b in held
        },
        "seed_config_sha256": sha(ROOT / "configs" / "seed.yaml"),
        "generator_sha256": sha(ROOT / "fixtures" / "generate.py"),
        "oracle_policy_sha256": sha(ROOT / "oracle" / "policy.py"),
    }


def verify_original() -> dict:
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))
    now = current()
    bad = [key for key in now if frozen.get(key) != now[key]]
    if bad:
        raise ValueError("Original frozen artifacts changed: " + ", ".join(bad))
    return frozen


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", required=True, action="store_true")
    parser.parse_args(argv)
    frozen = verify_original()
    print(f"OK: {len(frozen['heldout'])} held-out bundles match original hashes "
          f"(historical commit: {frozen['frozen_on_commit']})")


    return 0


if __name__ == "__main__":
    raise SystemExit(main())
