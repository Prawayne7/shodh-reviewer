"""Run CPU-only verification from the repository root with pinned packages installed."""
import os
import subprocess
import sys

COMMANDS = (
    ("pytest", "-q"),
    ("oracle.prospective",),
    ("oracle.check_labels",),
    ("fixtures.check_generated",),
    ("eval.run_eval", "--client", "fake", "--out", "reports/final.json"),
)


def main():
    for module in COMMANDS:
        print("python -X utf8 -m " + " ".join(module), flush=True)
        subprocess.run([sys.executable, "-X", "utf8", "-m", *module],
                       env={**os.environ, "PYTHONUTF8": "1"}, check=True)
        print("Exit 0", flush=True)


if __name__ == "__main__":
    main()
