"""
Script execute SRT voice generation using EverAI / Voice Pro logic.
Target SRT: C:\\Users\\User\\Downloads\\b0df0382-ddef-4d99-8ea8-840e4a4d8630.zh.srt
Voice: Vũ Thu Huệ (voice-9a0cb158-05f5-43fe) or configurable via CLI
"""
import sys
import os
import shutil
import argparse
from pathlib import Path

# Ensure UTF-8 stdout on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Add src to sys.path
sys.path.insert(0, r"D:\EverAI_PC_Working\src")

from everai.srt_studio.store import SrtProject, project_lock
from everai.srt_studio.runner import SrtRunner
from everai.srt_studio.model import canonical_config
from everai.engine import EverAITTSEngine
from everai.license_manager import LicenseManager
from everai.proxy_manager import ProxyCatalog
from everai.utils import StudioStore, application_data_dir, atomic_json, ensure_ffmpeg
from everai.voices import EVERAI_VOICES

def main():
    parser = argparse.ArgumentParser(description="Tạo voice cho SRT sử dụng logic Voice Pro")
    parser.add_argument("--srt", default=r"C:\Users\User\Downloads\b0df0382-ddef-4d99-8ea8-840e4a4d8630.zh.srt", help="Đường dẫn file SRT")
    parser.add_argument("--voice", default="voice-9a0cb158-05f5-43fe", help="ID giọng đọc (mặc định Vũ Thu Huệ)")
    parser.add_argument("--suffix", default="_VuThuHue", help="Hậu tố tên file xuất ra")
    parser.add_argument("--speed", type=float, default=0.9, help="Tốc độ đọc cơ bản")
    args = parser.parse_args()

    srt_path = Path(args.srt)
    if not srt_path.is_file():
        print(f"ERROR: SRT file not found: {srt_path}")
        sys.exit(1)

    voice_entry = next((v for v in EVERAI_VOICES if v['id'] == args.voice), None)
    voice_name = voice_entry['name'] if voice_entry else args.voice

    print(f"=== KHỞI TẠO TẠO GIỌNG ĐỌC CHO SRT ===")
    print(f"File SRT: {srt_path}")
    print(f"Giọng đọc: {voice_name} ({args.voice})")

    # 1. FFmpeg
    ffmpeg_bin = ensure_ffmpeg()
    print(f"FFmpeg binary: {ffmpeg_bin}")

    # 2. Store & Config
    store = StudioStore(application_data_dir())
    print(f"EverAI Studio data dir: {store.root}")

    # 3. License Manager
    lm = LicenseManager(store.root)
    bal = lm.balance()
    print(f"Số dư tài khoản: {bal['available']:,} ký tự khả dụng (tổng {bal['total']:,})")
    lm.ensure_active()

    # 4. Proxy Catalog
    catalog = ProxyCatalog(store.root / "proxies.json")
    active_proxies = [p.address for p in catalog.items if p.is_active]
    print(f"Proxy khả dụng: {active_proxies}")

    # 5. Audio Config
    model_id = store.settings.get("model_id", "everai-v1.6")
    pitch = float(store.settings.get("pitch", 1.0))
    bitrate = store.settings.get("bitrate", "128 kbps")
    
    config = dict(
        speaker_id=args.voice,
        model_id=model_id,
        speed=args.speed,
        pitch=pitch,
        volume=100,
        bitrate=bitrate,
        audio_type="mp3"
    )
    print(f"Cấu hình giọng đọc: {config}")

    # 6. Create or re-use SRT Project
    projects_dir = store.root / "srt-projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    print("\n--- Đang nạp và phân tích file SRT ---")
    project = SrtProject.create(projects_dir, srt_path)
    print(f"Project ID: {project.data['id']}")
    print(f"Tổng số cues: {len(project.data['cues'])}")
    for cue in project.data['cues']:
        print(f"  [Cue {cue['number']}] {cue['start_ms']}ms -> {cue['end_ms']}ms: {cue['text']}")

    # 7. Engine Factory
    common = dict(
        proxy_catalog=catalog,
        session_id=None,
        license_manager=lm,
        randomize_device=store.settings.get("randomize_device", True),
        download_via_direct_ip=store.settings.get("download_via_direct_ip", True),
        temp_dir=store.root / "samples/chunks",
        log_callback=lambda msg, lvl: print(f"[{lvl}] {msg}")
    )

    def factory(audio_config, concurrency):
        return EverAITTSEngine(**common, concurrency=concurrency)

    # 8. SrtRunner
    def runner_callback(kind, details):
        phase = details.get('phase', kind)
        completed = details.get('completed', 0)
        total = details.get('total', 0)
        cue_id = details.get('cue_id')
        print(f"--> [Tiến độ: {phase}] {completed}/{total} cues (cue: {cue_id})")

    workers = min(3, len(project.data['cues']))
    runner = SrtRunner(project, factory, lm, concurrency=workers, callback=runner_callback)

    # 9. Estimate
    est = runner.estimate(config)
    print(f"\nƯớc tính: {est['cues']} cues, {est['chunks']} đoạn, {est['characters']} ký tự, chi phí cần tính: {est['remaining']} ký tự")

    # 10. Process WAV
    stem = srt_path.stem + args.suffix
    dest_wav = srt_path.parent / f"{stem}.wav"
    print(f"\n--- Đang tạo và ghép nối timeline WAV: {dest_wav} ---")
    wav_result = runner.process(
        dest_wav,
        config,
        policy="bounded",
        max_tempo=1.35,
        output_format="wav"
    )
    print(f"✓ Đã xuất thành công file WAV: {wav_result} (Size: {wav_result.stat().st_size:,} bytes)")

    # 11. Process MP3 (Re-export from cached cues without re-calling TTS)
    dest_mp3 = srt_path.parent / f"{stem}.mp3"
    print(f"\n--- Đang xuất file MP3 tương ứng: {dest_mp3} ---")
    mp3_runner = SrtRunner(project, None, None, concurrency=1, callback=runner_callback)
    mp3_result = mp3_runner.process(
        dest_mp3,
        config,
        policy="bounded",
        max_tempo=1.35,
        output_format="mp3"
    )
    print(f"✓ Đã xuất thành công file MP3: {mp3_result} (Size: {mp3_result.stat().st_size:,} bytes)")

    # 12. Also copy to documents output dir
    output_dir = Path(store.settings.get("output_dir", r"C:\Users\User\Documents\EverAI Audio"))
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_wav = output_dir / dest_wav.name
    doc_mp3 = output_dir / dest_mp3.name
    shutil.copy2(dest_wav, doc_wav)
    shutil.copy2(dest_mp3, doc_mp3)
    print(f"✓ Đã lưu bản sao vào: {doc_wav} & {doc_mp3}")

    # 13. Update store state and srt-draft.json so Voice_Pro GUI sees it
    atomic_json(store.root / "srt-draft.json", {
        "mode": "text",
        "project": str(project.root),
        "policy": "bounded",
        "max_tempo": 1.25,
        "format": "wav",
        "cue_drafts": {}
    })
    try:
        store.add_result(wav_result, project_id=project.data["id"], revision=project.data["revision"], format="wav")
        store.add_result(mp3_result, project_id=project.data["id"], revision=project.data["revision"], format="mp3")
    except Exception as e:
        print(f"Warning: Could not record result to history: {e}")

    # Check updated balance
    bal_after = lm.balance()
    print(f"\n=== HOÀN TẤT XỬ LÝ ===")
    print(f"Số dư còn lại: {bal_after['available']:,} ký tự (đã dùng {bal['available'] - bal_after['available']} ký tự)")
    print(f"File kết quả:")
    print(f"1. WAV: {dest_wav}")
    print(f"2. MP3: {dest_mp3}")

if __name__ == "__main__":
    main()
