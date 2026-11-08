"""
Build Voice Pro Studio (White-label Rebranded Edition)
Eliminates user-facing EverAI references, renaming to 'Voice Pro Studio'.
Leaves the main EverAI source code completely intact.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
try:
    from .build_portable import sanitize_qt_runtime
except ImportError:
    from build_portable import sanitize_qt_runtime
from urllib.request import urlopen

IS_MAC = sys.platform == "darwin"


def brand_gui_source(source):
    """Change presentation labels while preserving provider IDs and stored config."""
    for old, new in (
        ('EverAI TTS Studio', 'Voice Pro Studio'), ('EverAI Studio', 'Voice Pro Studio'),
        ('Chọn nơi lưu dữ liệu EverAI', 'Chọn nơi lưu dữ liệu Voice Pro'),
        ('alias="EverAI_"', 'alias="VoicePro_"'),
        ('logging.getLogger("everai.studio."', 'logging.getLogger("voicepro.studio."'),
        ("PRIMARY_PROVIDER_LABEL = 'everai'", "PRIMARY_PROVIDER_LABEL = 'Voice Pro'"),
        ("PRIMARY_MODEL_PREFIX = 'everai-'", "PRIMARY_MODEL_PREFIX = 'VoicePro-'"),
    ):
        source = source.replace(old, new)
    return source


def build_voicepro(ffmpeg=None, destination=None):
    root = Path(__file__).resolve().parent
    # vendor/tts_python holds Windows-only compiled wheels; macOS uses pip packages instead.
    tts_dependencies = root / "vendor" / "tts_python"
    use_vendor = tts_dependencies.is_dir() and not IS_MAC
    if use_vendor:
        sys.path.insert(0, str(tts_dependencies))

    node_version = None

    try:
        from .tts.service import VOICES as voices
    except ImportError:
        from tts.service import VOICES as voices
    public_keys = root / "assets/license/public_keys.json"
    keys = json.loads(public_keys.read_text(encoding="utf-8"))
    if not keys:
        raise ValueError("Chưa có khóa cấp license.")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    destination = Path(destination).resolve() if destination else root / "dist" / "release-voicepro"
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

    with tempfile.TemporaryDirectory(prefix="voicepro-build-") as scratch:
        scratch = Path(scratch)
        package = scratch / "everai"
        package.mkdir()
        for name in (
            "__init__", "gui", "engine", "client", "utils", "proxy_manager", "voices",
            "device_profiles", "audio_player", "settings_dialog", "admin_log_dialog",
            "license_manager", "license_dialog", "ui_theme", "qt_gui", "qt_theme", "voice_picker"
        ):
            shutil.copyfile(root / (name + ".py"), package / (name + ".py"))

        tts_package = package / "tts"
        tts_package.mkdir()
        for source in (root / "tts").glob("*.py"):
            shutil.copyfile(source, tts_package / source.name)
        shutil.copytree(root / 'srt_studio', package / 'srt_studio',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

        # -------------------------------------------------------------
        # Rebranding transformations (EverAI -> Voice Pro Studio)
        # -------------------------------------------------------------
        print("Đang áp dụng nhận diện thương hiệu 'Voice Pro Studio'...")

        # 1. gui.py
        gui_p = package / "gui.py"
        code = brand_gui_source(gui_p.read_text(encoding="utf-8"))
        gui_p.write_text(code, encoding="utf-8")

        qt_path = package / "qt_gui.py"
        qt_source = qt_path.read_text(encoding="utf-8")
        qt_source = qt_source.replace("EverAI TTS Studio", "Voice Pro Studio")
        qt_source = qt_source.replace("PRIMARY_PROVIDER_LABEL = 'EverAI'", "PRIMARY_PROVIDER_LABEL = 'Voice Pro'")
        qt_source = qt_source.replace("PRIMARY_MODEL_PREFIX = 'everai-'", "PRIMARY_MODEL_PREFIX = 'VoicePro-'")
        qt_path.write_text(qt_source, encoding="utf-8")

        # 2. settings_dialog.py
        sd_p = package / "settings_dialog.py"
        sd = sd_p.read_text(encoding="utf-8")
        sd = sd.replace("EverAI TTS Studio 1.0", "Voice Pro Studio 1.0")
        sd = sd.replace("EverAI", "Voice Pro")
        sd_p.write_text(sd, encoding="utf-8")

        # 3. license_dialog.py
        ld_p = package / "license_dialog.py"
        ld = ld_p.read_text(encoding="utf-8")
        ld = ld.replace('[("EverAI license", "*.everai-license")]', '[("Voice Pro license", "*.voicepro-license"), ("License", "*.everai-license")]')
        ld = ld.replace('("EverAI license", "*.everai-license")', '("Voice Pro license", "*.voicepro-license")')
        ld = ld.replace("EverAI-wallet-backup.sqlite3", "VoicePro-wallet-backup.sqlite3")
        ld = ld.replace("EverAI license", "Voice Pro license")
        ld_p.write_text(ld, encoding="utf-8")

        # 4. client.py
        cl_p = package / "client.py"
        cl = cl_p.read_text(encoding="utf-8")
        cl = cl.replace("Không thể tải trang EverAI TTS", "Không thể kết nối máy chủ Voice Pro TTS")
        cl = cl.replace("Không tìm thấy CSRF token từ trang EverAI.", "Không tìm thấy token phiên Voice Pro.")
        cl = cl.replace("trang EverAI", "hệ thống Voice Pro")
        cl_p.write_text(cl, encoding="utf-8")

        # 5. engine.py
        eng_p = package / "engine.py"
        eng = eng_p.read_text(encoding="utf-8")
        eng = eng.replace("[ENGINE]", "[VOICE PRO]")
        eng_p.write_text(eng, encoding="utf-8")

        launcher = scratch / "launch.py"
        launcher.write_text(
            "import os, sys, traceback\n"
            "if sys.platform == 'win32':\n"
            "    _base = os.path.dirname(os.path.abspath(__file__))\n"
            "    _meipass = getattr(sys, '_MEIPASS', _base)\n"
            "    for _d in [\n"
            "        _base,\n"
            "        _meipass,\n"
            "        os.path.join(_meipass, 'PyQt6', 'Qt6', 'bin'),\n"
            "        os.path.join(_base, '_internal', 'PyQt6', 'Qt6', 'bin'),\n"
            "        os.path.join(_base, '_internal'),\n"
            "    ]:\n"
            "        if os.path.isdir(_d):\n"
            "            try:\n"
            "                os.add_dll_directory(_d)\n"
            "            except (AttributeError, OSError):\n"
            "                pass\n"
            "            os.environ['PATH'] = _d + os.pathsep + os.environ.get('PATH', '')\n"
            "try:\n"
            "    from everai.qt_gui import launch_gui\n"
            "    raise SystemExit(launch_gui())\n"
            "except Exception:\n"
            "    crash_log = os.environ.get('VOICEPRO_CRASH_LOG')\n"
            "    if crash_log:\n"
            "        with open(crash_log, 'w', encoding='utf-8') as stream:\n"
            "            traceback.print_exc(file=stream)\n"
            "    raise\n",
            encoding="utf-8"
        )

        notices = scratch / "THIRD_PARTY_NOTICES.txt"
        sample_stage = scratch / "voice_samples"
        sample_stage.mkdir()
        shutil.copyfile(samples / "manifest.json", sample_stage / "manifest.json")
        for record in sample_manifest["voices"].values():
            shutil.copyfile(samples / record["file"], sample_stage / record["file"])

        versions = {
            p: importlib.metadata.version(p)
            for p in ("requests", "curl_cffi", "cryptography", "customtkinter", "PyQt6", "pyinstaller")
        }
        license_info = subprocess.run([str(ffmpeg), "-L"], capture_output=True, text=True, check=True)
        build_info = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True, check=True)
        text = "Voice Pro Studio — Third party inventory\n\n" + json.dumps(versions, indent=2) + "\n\n"
        text += "Python / Qt 6 / PyQt6: see bundled runtime license files and package metadata.\n"
        for package_name in (
            "requests", "curl_cffi", "cryptography", "customtkinter", "PyQt6", "PyQt6-Qt6", "PyQt6-sip", "darkdetect", "packaging",
            "certifi", "cffi", "charset-normalizer", "idna", "urllib3", "pycparser",
            "typing-extensions", "tabulate"
        ):
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
        text += "\n--- Voice demo clips ---\nVoice demo clips for offline preview selection.\n"
        if provenance:
            text += "\nFFmpeg supplier and source record:\n" + json.dumps(provenance, indent=2) + "\n"
            for license_file in license_files:
                text += f"\n--- {license_file.name} ---\n" + license_file.read_text(encoding="utf-8")
        notices.write_text(text, encoding="utf-8")

        # PyInstaller build command with --name Voice_Pro
        data_sep = os.pathsep
        icon = root / ("assets/branding/voicepro.icns" if IS_MAC else "assets/branding/voicepro.ico")
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "Voice_Pro", "--distpath", str(scratch / "dist"),
            *(["--icon", str(icon)] if icon.is_file() else []),
            "--add-data", f"{root / 'assets/branding/voicepro-logo.png'}{data_sep}assets/branding",
            "--workpath", str(scratch / "work"), "--specpath", str(scratch),
            "--paths", str(scratch), "--add-binary", f"{ffmpeg.resolve()}{data_sep}bin",
            *(["--paths", str(tts_dependencies)] if use_vendor else []),
            *(["--osx-bundle-identifier", "vn.voicepro.studio"] if IS_MAC else []),
            "--hidden-import", "PyQt6.QtMultimedia",
            "--add-data", f"{sample_stage}{data_sep}assets/voice_samples",
            "--add-data", f"{public_keys}{data_sep}assets/license",
            "--add-data", f"{root / 'tts/providers/capcut_voices.json'}{data_sep}everai/tts/providers",
            "--add-data", f"{notices}{data_sep}.", "--collect-binaries", "curl_cffi",
            "--collect-data", "curl_cffi", "--hidden-import", "_cffi_backend",
            "--exclude-module", "customtkinter", "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
            "--collect-data", "certifi",
            "--exclude-module", "pytest", "--exclude-module", "seleniumbase",
            "--exclude-module", "numpy", "--exclude-module", "pandas",
            "--exclude-module", "IPython", "--exclude-module", "rich",
            "--exclude-module", "openpyxl", "--exclude-module", "DrissionPage",
            "--exclude-module", "aiohttp.pytest_plugin", "--exclude-module", "multidict._testcapi",
            str(launcher)
        ]
        build_env = dict(os.environ)
        if use_vendor:
            build_env["PYTHONPATH"] = str(tts_dependencies)
        print("Đang chạy PyInstaller đóng gói Voice_Pro.exe...")
        subprocess.run(command, cwd=scratch, env=build_env, check=True)

        if IS_MAC:
            bundle = scratch / "dist" / "Voice_Pro.app"
            executable = bundle / "Contents" / "MacOS" / "Voice_Pro"
        else:
            bundle = scratch / "dist" / "Voice_Pro"
            executable = bundle / "Voice_Pro.exe"

        # Verify built artifacts
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(executable))
        modules = archive.open_embedded_archive("PYZ.pyz").toc
        if IS_MAC:
            contents = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()}
        else:
            sanitize_qt_runtime(bundle)
            contents = {path.relative_to(bundle / "_internal").as_posix()
                        for path in (bundle / "_internal").rglob("*") if path.is_file()}
        def has_file(suffix):
            return any(name == suffix or name.endswith("/" + suffix) for name in contents)

        assert "everai.qt_gui" in modules and "everai.engine" in modules, "Missing runtime"
        assert {'everai.srt_studio.runner', 'everai.srt_studio.audio', 'everai.srt_studio.qt_panel',
                'everai.srt_studio.route', 'everai.srt_studio.smoke'} <= set(modules), 'Missing SRT timeline runtime'
        assert {"everai.tts.service", "everai.tts.engine", "everai.tts.capcut", "everai.tts.saydi"} <= set(modules)
        assert not any(name.startswith("src.") for name in modules)
        assert not any(name.endswith(("proxies.json", "cookies.json", ".log")) for name in contents)
        assert not any("admin_key" in name or "admin_tool" in name for name in modules)
        assert has_file("assets/license/public_keys.json")
        assert has_file("everai/tts/providers/capcut_voices.json")
        assert has_file("bin/ffmpeg" if IS_MAC else "bin/ffmpeg.exe")

        # Copy to destination directory
        if IS_MAC:
            # symlinks=True keeps the .app's framework links intact.
            shutil.copytree(bundle, destination / bundle.name, symlinks=True, dirs_exist_ok=True)
            executable = destination / bundle.name / "Contents" / "MacOS" / "Voice_Pro"
        else:
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

        # Write Voice Pro User Guide
        guide_path = destination / "HUONG_DAN.txt"
        guide_text = (
            "Voice Pro - chất lượng cao nhất — Bản phát hành chính thức 2026\n\n"
            "Giải nén nguyên gói, chạy Voice_Pro.exe. Giữ thư mục _internal cạnh file EXE.\n\n"
            "Tính năng chính:\n"
            "1. Dịch vụ TTS Voice Pro VIP tốc độ cao, hỗ trợ 66 giọng đọc tự nhiên (Bắc / Trung / Nam / Studio).\n"
            "2. Giọng bổ sung: CapCut.\n"
            "   Saydi: Chọn HN - Ngọc Huyền. Ứng dụng tự mở phiên khi bắt đầu tạo audio.\n"
            "3. Tính năng Hàng đợi TXT: Nạp hàng loạt file TXT, tự động đọc và xuất file MP3 theo danh sách.\n"
            "4. Độc lập 100%: Tích hợp sẵn FFmpeg, không cần cài đặt Python hay phần mềm ngoài.\n"
            "5. Cơ chế Anti-Detect: Tự động đổi hồ sơ thiết bị (User-Agent, TLS Fingerprint) và IP cho từng phân đoạn.\n\n"
            "6. Nhập SRT theo timeline: quản lý cue, nghe/sửa/tạo lại từng câu và xuất WAV/MP3.\n"
            "SRT: Tự động luôn xuất tối đa 1,35x; Nén mạnh tối đa 4x. Câu còn dài sẽ đẩy phần sau, không bị bỏ.\n"
            "Đổi timestamp/căn/xuất lại từ audio gốc không gọi TTS; sửa lời thoại hoặc Tạo lại cue có tính phí mới.\n\n"
            "CapCut kết nối trực tiếp; hồ sơ thiết bị Voice Pro không áp dụng cho provider này.\n"
            "Lưu ý: Không xóa hoặc đổi tên thư mục _internal cạnh file Voice_Pro.exe.\n"
        )
        if IS_MAC:
            guide_text = guide_text.replace(
                "Giải nén nguyên gói, chạy Voice_Pro.exe. Giữ thư mục _internal cạnh file EXE.",
                "Giải nén, kéo Voice_Pro.app vào Applications. Lần đầu mở: chuột phải > Open (bản chưa ký Apple).")
            guide_text = guide_text.replace(
                "Lưu ý: Không xóa hoặc đổi tên thư mục _internal cạnh file Voice_Pro.exe.\n", "")
        guide_path.write_text(guide_text, encoding="utf-8")

        report = dict(
            app_name="Voice Pro - chất lượng cao nhất",
            ui_toolkit="PyQt6",
            srt_timeline=dict(project_version=1, rate=48000, formats=['wav', 'mp3'], cue_jobs=True,
                              adaptive_fit_version=2, auto_tempo=1.35, force_tempo=4,
                              always_export=True, parallel_cues=True, shared_rotation_barrier=True),
            python=sys.version,
            packages=versions,
            ffmpeg=str(ffmpeg.resolve()),
            ffmpeg_sha256=hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
            exe_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            bytes=executable.stat().st_size,
            bundle_mode="app" if IS_MAC else "onedir",
            platform=sys.platform,
            node_version=node_version,
            tts_providers={'capcut': dict(voices=len(voices),
                                         languages=sorted({v['lang'] for v in voices}))}
        )
        (destination / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        
        zip_path = destination.with_suffix('.zip')
        print(f"Đang nén file ZIP: {zip_path}...")
        if IS_MAC:
            # ditto preserves symlinks and permissions that zipfile would flatten.
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(destination), str(zip_path)], check=True)
        else:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root_dir, _, files in os.walk(destination):
                    for f in files:
                        file_p = Path(root_dir) / f
                        arcname = file_p.relative_to(destination)
                        zf.write(file_p, arcname)
        print(f"Đã tạo file zip: {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MiB)")

        print(f"Đã build thành công Voice Pro Studio: {executable} ({executable.stat().st_size / 1024 / 1024:.1f} MiB)")
        return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg")
    parser.add_argument("--dist")
    args = parser.parse_args()
    build_voicepro(args.ffmpeg, args.dist)
