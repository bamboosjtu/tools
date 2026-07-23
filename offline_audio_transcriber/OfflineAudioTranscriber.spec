# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import importlib.metadata

import imageio_ffmpeg
from PyInstaller.utils.hooks import copy_metadata


project_dir = Path(SPEC).resolve().parent
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_distribution = importlib.metadata.distribution("imageio-ffmpeg")
ffmpeg_license_files = []
for installed_file in ffmpeg_distribution.files or []:
    if installed_file.name.lower().startswith(("license", "copying")):
        located = Path(ffmpeg_distribution.locate_file(installed_file))
        if located.is_file():
            ffmpeg_license_files.append((str(located), "third_party/ffmpeg"))

datas = copy_metadata("pywhispercpp")
datas += ffmpeg_license_files
datas += [
    (str(project_dir / "models" / "ggml-small.bin"), "models"),
    (str(project_dir / "glossary.example.txt"), "."),
    (str(project_dir / "LICENSE.txt"), "."),
    (str(project_dir / "THIRD_PARTY_NOTICES.txt"), "."),
]
binaries = [
    (ffmpeg, "bin"),
]
hiddenimports = ["_pywhispercpp"]

a = Analysis(
    ["transcriber_gui.py"],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pywhispercpp.examples",
        "matplotlib",
        "pandas",
        "IPython",
        "jupyter",
        "PIL",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OfflineAudioTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OfflineAudioTranscriber",
)
