# PyInstaller spec for the Windows Login Monitor MCP server.
# Build with:  pyinstaller build\server.spec --clean
# Output: dist\windows-login-monitor-mcp.exe (single self-contained file)

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# mcp + its sub-packages have a lot of dynamic imports - pull everything in.
mcp_datas, mcp_binaries, mcp_hidden = collect_all("mcp")
uvicorn_datas, uvicorn_binaries, uvicorn_hidden = collect_all("uvicorn")

block_cipher = None

a = Analysis(
    ["..\\mcp-server\\server.py"],
    pathex=["..\\mcp-server"],   # so `from auth_provider import ...` resolves
    binaries=mcp_binaries + uvicorn_binaries,
    datas=mcp_datas + uvicorn_datas,
    hiddenimports=[
        *mcp_hidden,
        *uvicorn_hidden,
        "auth_provider",
        "starlette.applications",
        "starlette.middleware.base",
        "starlette.requests",
        "starlette.responses",
        "starlette.routing",
        "httpx",
        "dotenv",
        # uvicorn protocol implementations
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.loops.asyncio",
        "uvicorn.loops.auto",
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
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
