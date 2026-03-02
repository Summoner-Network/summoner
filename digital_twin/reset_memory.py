#!/usr/bin/env python3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
DAYBYDAY_DIR = WORKSPACE_DIR / "daybyday"
OWNER_ID_PATH = BASE_DIR / "owner_id.json"

FILES = [
    WORKSPACE_DIR / "GOALS.md",
    WORKSPACE_DIR / "MEMORY.md",
    WORKSPACE_DIR / "USER.md",
    WORKSPACE_DIR / "SELF.md",
    WORKSPACE_DIR / "SOUL.md",
    WORKSPACE_DIR / "REPORT.md",
]


def _empty_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _clear_daybyday(folder: Path) -> None:
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        return
    for f in folder.iterdir():
        if f.is_file():
            f.unlink()


def main() -> None:
    for f in FILES:
        _empty_file(f)

    _clear_daybyday(DAYBYDAY_DIR)

    if OWNER_ID_PATH.exists():
        OWNER_ID_PATH.unlink()

    print("Reset complete:")
    for f in FILES:
        print(f"- emptied {f}")
    print(f"- cleared {DAYBYDAY_DIR}")
    print(f"- removed {OWNER_ID_PATH}")


if __name__ == "__main__":
    main()
