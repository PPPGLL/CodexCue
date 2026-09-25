# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import uiautomation

uiautomation_bin = Path(uiautomation.__file__).resolve().parent / 'bin'
uiautomation_dlls = [(str(path), 'uiautomation/bin')
                      for path in uiautomation_bin.glob('UIAutomationClient_VC140_*.dll')]

a = Analysis(
    ['run_companion.py'],
    pathex=['src'],
    binaries=uiautomation_dlls,
    datas=[],
    hiddenimports=collect_submodules('keyring.backends') + ['uiautomation'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
# Qt6Core on Windows uses the system ICU (icuuc.dll). PyInstaller can pick up
# an unrelated ICU build from PATH (for example Poppler), whose exports are
# incompatible with Qt. Never bundle that unrelated copy.
a.binaries = [entry for entry in a.binaries
              if entry[0].lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='CodexCue', console=False,
          icon=str(Path('assets') / 'companion.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='CodexCue')
