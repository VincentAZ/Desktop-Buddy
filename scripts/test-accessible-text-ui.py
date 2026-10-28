#!/usr/bin/env python3
"""Real AT-SPI field replacement through Buddy's native approval dialog."""
from pathlib import Path
import runpy,sys
sys.argv.append('--edit-text')
runpy.run_path(str(Path(__file__).with_name('test-desktop-approval-ui.py')),run_name='__main__')
