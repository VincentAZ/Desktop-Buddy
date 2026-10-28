#!/usr/bin/env python3
"""Detect a real GTK field that accepts only part of the requested text."""
from pathlib import Path
import runpy,sys
sys.argv.extend(['--edit-text','--truncated-text'])
runpy.run_path(str(Path(__file__).with_name('test-desktop-approval-ui.py')),run_name='__main__')
