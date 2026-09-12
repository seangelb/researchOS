"""Gaming entry point for the shared offline checker; one set of guardrails."""
import argparse
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("researchos_notebook_checker", Path(__file__).resolve().parents[2] / "scripts/check_notebooks.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
ACTIVE_NOTEBOOKS = checker.GAMING_NOTEBOOKS
APPROVAL_BLOCK = checker.APPROVAL_BLOCK
offline_guards = checker.offline_guards
run_notebook = checker.run_notebook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--include-reference", action="store_true")
    args = parser.parse_args()
    options = ["--root", str(args.root.resolve().parent), "--project", "gaming"]
    if args.include_reference:
        options.append("--include-reference")
    return checker.main(options)


if __name__ == "__main__":
    sys.exit(main())
