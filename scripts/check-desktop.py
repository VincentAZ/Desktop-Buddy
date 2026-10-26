#!/usr/bin/env python3
"""Explicit, safe acceptance probes. No settings changes, installations or root."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_buddy.desktop import Portal,Accessibility
from desktop_buddy.browser import Browser
from desktop_buddy.security import StopToken

p=argparse.ArgumentParser()
p.add_argument('--observe',action='store_true',help='Request screenshot through GNOME consent')
p.add_argument('--connect',action='store_true',help='Request GNOME screen/input consent; no input events are sent')
p.add_argument('--browser',metavar='URL',help='Navigate an isolated visible Chrome instance and report the final page')
p.add_argument('--output',default='.test-data/manual')
a=p.parse_args();root=Path(a.output).resolve();root.mkdir(parents=True,exist_ok=True)
token=StopToken();portal=None;browser=None
try:
 if a.connect or a.observe:
  portal=Portal()
  if a.connect:print(json.dumps(portal.connect(token),indent=2))
  result=portal.screenshot(root/'desktop.png',token);print(json.dumps(result,indent=2));(root/'screenshot.json').write_text(json.dumps(result,indent=2))
 if a.browser:
  browser=Browser(root/'browser-profile');result=browser.navigate(a.browser,token)
  print(json.dumps(result,indent=2,ensure_ascii=False));(root/'browser.json').write_text(json.dumps(result,indent=2));time.sleep(2)
 if not (a.connect or a.observe or a.browser):
  print(json.dumps(Accessibility().inspect(depth=1),indent=2))
finally:
 if portal:portal.close()
 if browser:browser.close()
