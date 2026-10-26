"""Writable defaults for both a source checkout and an installed package."""
import os
from pathlib import Path


def source_root():
    root = Path(__file__).resolve().parent.parent
    return root if (root / 'pyproject.toml').is_file() and (root / 'launch.sh').is_file() else None


def default_data_dir():
    checkout = source_root()
    if checkout:
        return checkout / '.buddy'
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    if not base.is_absolute():
        base = Path.home() / '.local' / 'share'
    return base / 'desktop-buddy'


def default_workspace(data_dir):
    checkout = source_root()
    return checkout / 'workspace' if checkout else Path(data_dir) / 'workspace'
