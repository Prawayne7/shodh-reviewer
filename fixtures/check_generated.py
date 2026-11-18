"""Cross-platform reproduction check for the original, byte-frozen dataset."""
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from fixtures.generate import ROOT, generate


def tree(path):
    return {
        f.relative_to(path).as_posix(): f.read_bytes().replace(b"\r\n", b"\n")
        for f in path.rglob("*")
        if f.is_file() and f.name != ".gitkeep" and not f.name.startswith("heldout_manifest")
    }


def main():
    config = yaml.safe_load((ROOT / "configs/seed.yaml").read_text(encoding="utf-8"))
    with TemporaryDirectory() as scratch:
        generated = Path(scratch)
        generate(generated, config["seed"])
        for folder in ("fixtures/bundles", "labels", "fixtures/model_scripts"):
            if tree(generated / folder) != tree(ROOT / folder):
                raise SystemExit(f"Dataset mismatch in {folder}")
    print("OK: committed dataset equals regenerated dataset (line endings normalized)")


if __name__ == "__main__":
    main()
