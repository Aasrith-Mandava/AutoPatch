"""
File I/O and backup management.

Reads source files, creates backups before modification, and writes fixes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sonar_agent.core import config


def resolve_path(relative_path: str) -> Path:
    """Resolve a relative file path against the configured PROJECT_PATH."""
    return Path(config.PROJECT_PATH) / relative_path


def read_source_file(relative_path: str) -> str:
    """
    Read the full contents of a source file.

    Raises FileNotFoundError with a clear message if the path is invalid.
    """
    full_path = resolve_path(relative_path)
    if not full_path.is_file():
        raise FileNotFoundError(
            f"Source file not found: {full_path}\n"
            f"  Relative path: {relative_path}\n"
            f"  PROJECT_PATH:  {config.PROJECT_PATH}"
        )
    return full_path.read_text(encoding="utf-8")


def create_backup(relative_path: str) -> Path:
    """
    Create a backup of the original file before applying a fix.

    Backups are stored under PROJECT_PATH / BACKUP_DIR / <original_name>.bak
    If a backup already exists, a numeric suffix is added.

    Returns the Path to the backup file.
    """
    source = resolve_path(relative_path)
    backup_dir = Path(config.PROJECT_PATH) / config.BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Flatten directory separators so backup names are unique
    safe_name = relative_path.replace("/", "__").replace("\\", "__")
    backup_path = backup_dir / f"{safe_name}.bak"

    # Handle collisions with a numeric suffix
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{safe_name}.{counter}.bak"
        counter += 1

    shutil.copy2(source, backup_path)
    return backup_path


def write_fixed_file(relative_path: str, content: str) -> Path:
    """
    Overwrite the source file with the fixed content.

    Returns the Path to the written file.
    """
    full_path = resolve_path(relative_path)
    full_path.write_text(content, encoding="utf-8")
    return full_path
