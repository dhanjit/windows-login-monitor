# PyInstaller spec for the Windows Login Monitor MCP server.
# Build with:  pyinstaller build\server.spec --clean
# Output: dist\windows-login-monitor-mcp.exe (single self-contained file)

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# mcp + its sub-packages have a lot of dynamic imports - pull everything in.
mcp_datas, mcp_binaries, mcp_hidden = collect_all("mcp")

# SDK 2.x moved the wire types out to their own top-level package. collect_all
# on "mcp" does not reach it, and the miss only shows up at runtime.
types_datas, types_binaries, types_hidden = collect_all("mcp_types")

block_cipher = None

a = Analysis(
    ["..\\mcp-server\\server.py"],
    pathex=["..\\mcp-server"],
    binaries=[*mcp_binaries, *types_binaries],
    datas=[*mcp_datas, *types_datas],
    hiddenimports=[
        *mcp_hidden,
        *types_hidden,
        "httpx2",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="windows-login-monitor-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # MUST stay True. A windowed PyInstaller build gives the process no usable
    # stdout, and stdio is the only transport this server has. The old
    # windowless build existed because it ran as a background service; nothing
    # runs it that way now, and clients spawn it with the window hidden anyway.
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
