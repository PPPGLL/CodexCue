"""Fail CI if packaged Qt or UI Automation libraries cannot load on Windows."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def main() -> None:
    package = (Path(sys.argv[1]) if len(sys.argv) > 1 else
               Path(__file__).resolve().parent.parent / 'dist' / 'CodexCue')
    root = package.resolve() / '_internal'
    if not root.is_dir():
        raise FileNotFoundError(root)
    handles = [os.add_dll_directory(str(root)),
               os.add_dll_directory(str(root / 'PySide6')),
               os.add_dll_directory(str(root / 'shiboken6'))]
    try:
        for name in ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll'):
            ctypes.WinDLL(str(root / 'PySide6' / name))
            print(f'loaded {name}')
        arch = 'X64' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'X86'
        name = f'UIAutomationClient_VC140_{arch}.dll'
        ctypes.WinDLL(str(root / 'uiautomation' / 'bin' / name))
        print(f'loaded {name}')
        tokenizer = list((root / 'tiktoken').glob('_tiktoken*.pyd'))
        if len(tokenizer) != 1:
            raise FileNotFoundError('Packaged tiktoken native extension is missing or ambiguous')
        ctypes.WinDLL(str(tokenizer[0]))
        print('loaded tiktoken native extension')
    finally:
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
