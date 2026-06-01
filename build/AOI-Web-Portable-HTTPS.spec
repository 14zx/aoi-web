# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

# Spec лежит в ``build/``; PyInstaller выполняет его с cwd ``build``, без этого
# ``import app`` / ``import scripts`` не работают и ``collect_submodules`` даёт [].
_ROOT = os.path.normpath(os.path.join(SPECPATH, '..'))
sys.path.insert(0, _ROOT)

# Статику кладём в ``web_static``, а не в ``app/static``: иначе на диске появляется
# каталог ``app`` только со ``static``, и он перекрывает импорт пакета ``app`` из архива.
datas = [
    (os.path.join(_ROOT, 'app', 'static'), 'web_static'),
    (os.path.join(_ROOT, 'models'), 'models'),
    (os.path.join(_ROOT, 'certs'), 'certs'),
    (os.path.join(_ROOT, 'storage'), 'storage'),
    (os.path.join(_ROOT, 'aoi.db'), '.'),
    (os.path.join(_ROOT, '.env'), '.'),
    # Wrapper: double-click on .exe leaves cwd wrong; user can use this .bat too.
    (os.path.join(SPECPATH, 'launch_portable_https.bat'), '.'),
]
hiddenimports = [
    'passlib.handlers.bcrypt',
    'ultralytics',
    'ultralytics.models.yolo.detect',
    'ultralytics.models.yolo.segment',
    'ultralytics.nn.tasks',
    'lap',
]
datas += collect_data_files('ultralytics')
datas += collect_data_files('cv2')
hiddenimports += collect_submodules('app')
hiddenimports += collect_submodules('scripts')
hiddenimports += collect_submodules('zeroconf')


a = Analysis(
    ['..\\scripts\\aoi_https_frozen.py'],
    pathex=[_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['datasets', 'huggingface_hub', 'torchaudio', 'tensorboard', 'pytest', 'matplotlib.backends.backend_tkagg'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AOI-Web-Portable-HTTPS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AOI-Web-Portable-HTTPS',
)
