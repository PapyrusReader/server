"""Snapshot the existing flat web deployment without deleting or modifying its files."""

import argparse
import os
import shutil
import tempfile
from pathlib import Path


def migrate(root: Path) -> None:
    current = root / "current"

    if current.is_symlink() and (current / "index.html").is_file():
        return

    if current.exists() or current.is_symlink():
        raise ValueError("An invalid current entry already exists; inspect it before migrating")

    if not (root / "index.html").is_file():
        raise ValueError("Extract an initial web release into this directory before migrating")

    sources = [path for path in root.iterdir() if path.name != "releases" and not path.name.startswith(".")]

    if any(path.is_symlink() or any(child.is_symlink() for child in path.rglob("*")) for path in sources):
        raise ValueError("The initial web deployment must not contain symlinks")

    releases = root / "releases"
    releases.mkdir(exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix="initial-", dir=releases))

    try:
        for source in sources:
            if source.is_dir():
                shutil.copytree(source, snapshot / source.name)
            else:
                shutil.copy2(source, snapshot / source.name)

        snapshot.chmod(0o755)
        link = root / ".current-next"
        link.symlink_to(f"releases/{snapshot.name}")
        os.replace(link, current)
    except BaseException:
        shutil.rmtree(snapshot)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent / "web")
    args = parser.parse_args()
    migrate(args.root)
    print("Versioned web layout ready; original flat files remain intact")


if __name__ == "__main__":
    main()
