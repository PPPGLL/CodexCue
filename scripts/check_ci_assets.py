"""Check draft artifacts and provenance; this does not replace desktop acceptance."""
import argparse
import json
from pathlib import Path

from check_release import check_ci


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(check_ci(args.assets, args.commit, args.version), indent=2))
