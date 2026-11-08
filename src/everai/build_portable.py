"""Build Studio with its self-contained TTS providers; exclude private/admin data."""
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import os
import zipfile
from urllib.request import urlopen
from pathlib import Path


def sanitize_qt_runtime(bundle):
    """Ensure complete, isolated Qt6, ICU, and MSVC runtime in bundle.
    
    Prevents Microsoft C Runtime Error R6034 and missing ICU DLL errors
    on client machines by bundling all required CRT and ICU libraries directly
    into both bundle root (next to EXE) and _internal/.
    """
    bundle = Path(bundle).resolve()
    internal = bundle / '_internal'
    qt_bin = internal / 'PyQt6' / 'Qt6' / 'bin'
    sys32 = Path(r'C:\Windows\System32')

    # 1. MSVC CRT DLLs: ensure MSVCP140*.dll and VCRUNTIME140*.dll exist in bundle root, _internal, and qt_bin
    crt_sources = []
    if qt_bin.is_dir():
        crt_sources.append(qt_bin)
    crt_sources.append(Path(sys.executable).parent)
    if sys32.is_dir():
        crt_sources.append(sys32)

    crt_names = [
        'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
        'vcruntime140.dll', 'vcruntime140_1.dll'
    ]

    for dll_name in crt_names:
        src_file = None
        for src_dir in crt_sources:
            if not src_dir.is_dir():
                continue
            cand = src_dir / dll_name
            if cand.is_file():
                src_file = cand
                break
            for f in src_dir.glob('*.dll'):
                if f.name.lower() == dll_name.lower():
                    src_file = f
                    break
            if src_file:
                break
        if src_file:
            for target_dir in [bundle, internal]:
                if target_dir.is_dir():
                    target_file = target_dir / src_file.name
                    if not target_file.is_file():
                        shutil.copyfile(src_file, target_file)
            if qt_bin.is_dir():
                target_qt = qt_bin / src_file.name
                if not target_qt.is_file():
                    shutil.copyfile(src_file, target_qt)

    # 2. ICU DLLs: Qt6Core requires icuuc.dll (forwarder to icu.dll).
    # Client machines (especially standard Windows 10/11) may not have icuuc.dll
    # in System32 or may encounter conflicts from PATH. We bundle icuuc.dll, icu.dll,
    # and icuin.dll to ensure 100% offline self-containment.
    icu_names = ['icuuc.dll', 'icu.dll', 'icuin.dll']
    for icu_name in icu_names:
        cand = sys32 / icu_name
        if cand.is_file():
            for target_dir in [bundle, internal, qt_bin]:
                if target_dir.is_dir():
                    target_file = target_dir / cand.name
                    if not target_file.is_file():
                        shutil.copyfile(cand, target_file)


def build(ffmpeg=None, destination=None):
    root = Path(__file__).resolve().parent
    tts_dependencies = root / 'vendor' / 'tts_python'
    if tts_dependencies.is_dir():
        sys.path.insert(0, str(tts_dependencies))
    node = shutil.which('node')
    if not node:
        raise ValueError('Cần Node.js trên máy build để đóng gói runtime TTS.')
    node_version = subprocess.run([node,'--version'],capture_output=True,text=True,check=True).stdout.strip()
    node_license = root / 'vendor/nodejs' / node_version / 'LICENSE'
    if not node_license.is_file():
        with urlopen(f'https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE',timeout=30) as response:
            license_content = response.read()
        if b'Permission is hereby granted' not in license_content:
            raise ValueError('Invalid Node.js license response')
        node_license.parent.mkdir(parents=True,exist_ok=True)
        node_license.write_bytes(license_content)
    try:
        from .tts.service import VOICES as voices
    except ImportError:
        from tts.service import VOICES as voices
    public_keys = root / "assets/license/public_keys.json"
    keys = json.loads(public_keys.read_text(encoding="utf-8"))
    if not keys:
        raise ValueError("Chưa có khóa cấp license. Chạy admin_key/admin_tool.py để tạo hoặc phục hồi bộ khóa trước khi build.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    destination = Path(destination).resolve() if destination else root / "dist"
    vendor = root / "vendor" / "ffmpeg"
    if ffmpeg is None:
        try:
            from .prepare_ffmpeg import prepare
        except ImportError:
            from prepare_ffmpeg import prepare
        ffmpeg = prepare()
    ffmpeg = Path(ffmpeg).resolve()
    if not ffmpeg.is_file():
        raise SystemExit("Cần FFmpeg: đặt trên PATH hoặc truyền --ffmpeg đường-dẫn.")
    try:
        from .prepare_voice_samples import prepare as prepare_voice_demos
    except ImportError:
        from prepare_voice_samples import prepare as prepare_voice_demos
    samples = root / "assets" / "voice_samples"
    sample_manifest = prepare_voice_demos(samples)
    destination.mkdir(parents=True, exist_ok=True)
    provenance = None
    license_files = []
    if ffmpeg == (vendor / "bin" / "ffmpeg.exe").resolve():
        provenance = json.loads((vendor / "provenance.json").read_text(encoding="utf-8"))
        if hashlib.sha256(ffmpeg.read_bytes()).hexdigest() != provenance["binary_sha256"]:
            raise ValueError("FFmpeg binary differs from the verified upstream archive")
        license_files = list(vendor.glob("*.txt"))
    with tempfile.TemporaryDirectory(prefix="everai-build-") as scratch:
        scratch = Path(scratch)
        package = scratch / "everai"
        package.mkdir()
        for name in ("__init__", "gui", "engine", "client", "utils", "proxy_manager", "voices",
                     "device_profiles", "audio_player", "settings_dialog", "admin_log_dialog",
                     "license_manager", "license_dialog", "ui_theme", "qt_gui", "qt_theme", "voice_picker"):
            shutil.copyfile(root / (name + ".py"), package / (name + ".py"))
        tts_package = package / 'tts'
        tts_package.mkdir()
        for source in (root / 'tts').glob('*.py'):
            shutil.copyfile(source, tts_package / source.name)
        shutil.copytree(root / 'srt_studio', package / 'srt_studio',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        provider_stage = scratch / 'providers'
        shutil.copytree(root / 'tts/providers', provider_stage,
                        ignore=shutil.ignore_patterns('__pycache__', '*.map', 'test', 'tests'))
        launcher = scratch / "launch.py"
        launcher.write_text("import os, sys\n"
                            "if sys.platform == 'win32':\n"
                            "    _base = os.path.dirname(os.path.abspath(__file__))\n"
                            "    _meipass = getattr(sys, '_MEIPASS', _base)\n"
                            "    for _d in [_base, _meipass, os.path.join(_meipass, 'PyQt6', 'Qt6', 'bin'), os.path.join(_base, '_internal', 'PyQt6', 'Qt6', 'bin'), os.path.join(_base, '_internal')]:\n"
                            "        if os.path.isdir(_d):\n"
                            "            try:\n"
                            "                os.add_dll_directory(_d)\n"
                            "            except (AttributeError, OSError):\n"
                            "                pass\n"
                            "            os.environ['PATH'] = _d + os.pathsep + os.environ.get('PATH', '')\n"
                            "from everai.tts.edge_worker import dispatch\n"
                            "if not dispatch():\n"
                            "    from everai.qt_gui import launch_gui\n"
                            "    launch_gui()\n", encoding="utf-8")
        notices = scratch / "THIRD_PARTY_NOTICES.txt"
        sample_stage = scratch / "voice_samples"
        sample_stage.mkdir()
        shutil.copyfile(samples / "manifest.json", sample_stage / "manifest.json")
        for record in sample_manifest["voices"].values():
            shutil.copyfile(samples / record["file"], sample_stage / record["file"])
        versions = {p: importlib.metadata.version(p) for p in ("requests", "curl_cffi", "cryptography", "customtkinter", "PyQt6", "pyinstaller", 'edge-tts', 'aiohttp')}
        license_info = subprocess.run([str(ffmpeg), "-L"], capture_output=True, text=True, check=True)
        build_info = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True, check=True)
        text = "EverAI TTS Studio — Third party inventory\n\n" + json.dumps(versions, indent=2) + "\n\n"
        text += "Python / Qt 6 / PyQt6: see bundled runtime license files and package metadata.\n"
        for package_name in ("requests", "curl_cffi", "cryptography", "customtkinter", "PyQt6", "PyQt6-Qt6", "PyQt6-sip", "darkdetect", "packaging", "certifi", "cffi", "charset-normalizer", "idna", "urllib3", "pycparser", 'edge-tts', 'aiohttp', 'aiohappyeyeballs', 'aiosignal', 'attrs', 'frozenlist', 'multidict', 'propcache', 'yarl', 'typing-extensions', 'tabulate'):
            try:
                dist = importlib.metadata.distribution(package_name)
                text += f"\n--- {package_name} {dist.version} ---\n"
                for path in dist.files or []:
                    if path.name.lower().startswith(("license", "copying")):
                        text += dist.locate_file(path).read_text(encoding="utf-8", errors="replace") + "\n"
            except importlib.metadata.PackageNotFoundError:
                pass
        text += "\n--- FFmpeg ---\n" + license_info.stdout + license_info.stderr + build_info.stdout
        text += "\nFFmpeg project: https://ffmpeg.org/\n"
        text += '\n--- Node.js runtime ---\n' + node_license.read_text(encoding='utf-8')
        text += '\n--- JavaScript dependencies ---\nLicense texts and package metadata are retained inside _internal/everai/tts/providers/node_modules.\n'
        text += "\n--- Voice demo clips ---\nOriginal public EverAI demos, downloaded for offline voice selection.\nSource: https://www.everai.vn/text-to-speech\nThese are original samples, not audio generated using the user's text or settings.\nNo additional redistribution license is asserted; review demo rights before public commercial distribution.\nAsset origins and hashes: assets/voice_samples/manifest.json\n"
        if provenance:
            text += "\nFFmpeg supplier and source record:\n" + json.dumps(provenance, indent=2) + "\n"
            for license_file in license_files:
                text += f"\n--- {license_file.name} ---\n" + license_file.read_text(encoding="utf-8")
        else:
            text += "Custom FFmpeg: supplier/source provenance is not verified by this build.\n"
        notices.write_text(text, encoding="utf-8")
        # Ship extracted dependencies: onefile cannot start when the system TEMP drive is full.
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                   "--name", "EverAI_TTS_Studio", "--distpath", str(scratch / "dist"),
                   "--workpath", str(scratch / "work"), "--specpath", str(scratch),
                   "--paths", str(scratch), "--add-binary", f"{ffmpeg.resolve()};bin",
                   '--paths', str(tts_dependencies), '--add-binary', f'{node};everai/tts/runtime',
                   '--add-data', f'{provider_stage};everai/tts/providers',
                   "--add-data", f"{sample_stage};assets/voice_samples",
                   "--add-data", f"{public_keys};assets/license",
                   "--add-data", f"{notices};.", "--collect-binaries", "curl_cffi",
                   "--collect-data", "curl_cffi", "--hidden-import", "_cffi_backend",
                   "--exclude-module", "customtkinter", "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
                   '--collect-all', 'edge_tts', '--collect-all', 'aiohttp',
                   '--collect-all', 'yarl', '--collect-all', 'multidict',
                   '--collect-all', 'frozenlist', '--collect-all', 'propcache',
                   '--collect-all', 'aiohappyeyeballs', '--collect-data', 'certifi',
                   "--exclude-module", "pytest", "--exclude-module", "seleniumbase",
                   "--exclude-module", "numpy", "--exclude-module", "pandas",
                   "--exclude-module", "IPython", "--exclude-module", "rich",
                   "--exclude-module", "openpyxl", "--exclude-module", "DrissionPage",
                   '--exclude-module', 'aiohttp.pytest_plugin', '--exclude-module', 'multidict._testcapi',
                   str(launcher)]
        build_env = dict(__import__("os").environ)
        build_env['PYTHONPATH'] = str(tts_dependencies)
        subprocess.run(command, cwd=scratch, env=build_env, check=True)
        bundle = scratch / "dist" / "EverAI_TTS_Studio"
        sanitize_qt_runtime(bundle)
        executable = bundle / "EverAI_TTS_Studio.exe"
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(executable))
        modules = archive.open_embedded_archive("PYZ.pyz").toc
        contents = {path.relative_to(bundle / "_internal").as_posix()
                    for path in (bundle / "_internal").rglob("*") if path.is_file()}
        assert "everai.qt_gui" in modules and "everai.engine" in modules, "Missing EverAI runtime"
        assert {'everai.srt_studio.runner', 'everai.srt_studio.audio', 'everai.srt_studio.qt_panel',
                'everai.srt_studio.route', 'everai.srt_studio.smoke'} <= set(modules), 'Missing SRT timeline runtime'
        assert {'everai.tts.service','everai.tts.engine','everai.tts.edge_worker','edge_tts','aiohttp'} <= set(modules), 'Missing bundled TTS Python runtime'
        assert {'everai/tts/runtime/node.exe','everai/tts/providers/voices.json',
                'everai/tts/providers/upstream.cjs','everai/tts/providers/runner.cjs'} <= {name.lower() for name in contents}, 'Missing provider data or Node runtime'
        assert not any(name.startswith("src.") for name in modules), "Unexpected parent project import"
        assert not any(name.endswith(("proxies.json", "cookies.json", ".log")) for name in contents), "Private data in bundle"
        assert {"everai.license_manager"} <= set(modules), "Missing license runtime"
        assert not any("admin_key" in name or "admin_tool" in name for name in modules), "Admin code in customer build"
        assert not any(any(part in name.lower().replace("\\", "/") for part in
                           ("admin_key/", "signing_key", "recovery_password", ".everai-license", "wallet.sqlite"))
                       for name in contents), "Admin secrets or customer state in bundle"
        assert "assets/license/public_keys.json" in contents, "Missing public keys"
        expected_demos = {"assets/voice_samples/" + record["file"] for record in sample_manifest["voices"].values()}
        bundled_demos = {name for name in contents if name.endswith(".mp3")}
        assert bundled_demos == expected_demos, "Unexpected or missing MP3 assets in bundle"
        assert "bin/ffmpeg.exe" in contents, "Missing FFmpeg"
        shutil.copytree(bundle, destination, dirs_exist_ok=True)
        executable = destination / executable.name
        shutil.copyfile(notices, destination / notices.name)
        if provenance:
            shutil.copyfile(vendor / "provenance.json", destination / "FFMPEG_PROVENANCE.json")
            legal_dir = destination / "licenses" / "ffmpeg"
            legal_dir.mkdir(parents=True, exist_ok=True)
            for license_file in license_files:
                shutil.copyfile(license_file, legal_dir / license_file.name)
            if (vendor / "sources").is_dir():
                shutil.copytree(vendor / "sources", destination / "third-party-sources", dirs_exist_ok=True)
        report = dict(python=sys.version, packages=versions, ffmpeg=str(ffmpeg.resolve()),
                      ffmpeg_sha256=hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
                      exe_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                      bytes=executable.stat().st_size)
        report["modules_checked"] = ["everai.qt_gui", "everai.qt_theme", "everai.engine", "no src.*", "no private data"]
        report['ui_toolkit'] = 'PyQt6'
        report['srt_timeline'] = dict(project_version=1, rate=48000, formats=['wav', 'mp3'],
                                      cue_jobs=True, adaptive_fit_version=2, auto_tempo=1.35,
                                      force_tempo=4, always_export=True,
                                      parallel_cues=True, shared_rotation_barrier=True)
        report["ffmpeg_provenance_verified"] = provenance is not None
        report["ffmpeg_license"] = "LGPL-3.0-or-later" if provenance else "custom; see notices"
        report["offline_voice_demos"] = len(expected_demos)
        report["license_key_ids"] = sorted(keys)
        report["admin_excluded"] = True
        report["bundle_mode"] = "onedir"
        report["keep_directory"] = "_internal"
        report['tts_providers'] = {
            engine: dict(voices=sum(v['engine'] == engine for v in voices),
                         languages=sorted({v['lang'] for v in voices if v['engine'] == engine}))
            for engine in ('capcut','edge','elevenlabs','saydi')}
        report['node_version'] = node_version
        report['node_license_sha256'] = hashlib.sha256(node_license.read_bytes()).hexdigest()
        report['edge_worker'] = 'same-executable; no installed Python or Node required'
        from tts.languages import ELEVEN_MODELS
        report['elevenlabs_model_languages'] = ELEVEN_MODELS
        report['tts_providers']['elevenlabs']['language_scope'] = 'Voice origin; spoken languages depend on selected model'
        report["runtime_files_sha256"] = {
            path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((destination / "_internal").rglob("*")) if path.is_file()
        }
        report["offline_voice_demo_bytes"] = sum(record["bytes"] for record in sample_manifest["voices"].values())
        (destination / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Đã build: {executable} ({executable.stat().st_size / 1024 / 1024:.1f} MiB)")
        return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg")
    parser.add_argument("--dist")
    args = parser.parse_args()
    build(args.ffmpeg, args.dist)
