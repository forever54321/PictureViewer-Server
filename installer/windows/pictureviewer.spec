# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PictureViewer Server (Windows)

import os

block_cipher = None
server_dir = os.path.join('..', '..')

a = Analysis(
    ['launcher.py'],
    pathex=[server_dir],
    binaries=[],
    datas=[
        (os.path.join(server_dir, 'server.py'), '.'),
        (os.path.join(server_dir, 'config.py'), '.'),
    ],
    hiddenimports=[
        'flask', 'flask_cors', 'PIL', 'PIL.Image', 'PIL.ImageOps',
        'jwt', 'dotenv', 'werkzeug',
        'werkzeug.serving', 'werkzeug.debug',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'pytest'],
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
    name='PictureViewerServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join('..', 'icon', 'AppIcon.ico'),
)
