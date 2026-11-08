"""Regression checks for durable desktop workflows (no external service calls)."""
import json
import threading
import time
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.everai import utils
from src.everai.engine import EverAITTSEngine
from src.everai.client import EverAIClient, EverAIConnectionError, EverAICancelled, EverAIQuotaError
from src.everai.audio_player import MiniAudioPlayer
from src.everai.license_manager import LicenseError
from src.everai.proxy_manager import ProxyCatalog, ProxyItem

pytestmark = pytest.mark.usefixtures("funded_license")


def fake_concat(files, destination, **kwargs):
    destination.write_bytes(b"".join(path.read_bytes() for path in files))
    return destination


def test_main_worker_selection_shares_settings_and_reaches_engine(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    app = EverAIGuiApp(root, tmp_path)
    try:
        root.geometry("900x600")
        root.update()
        assert app.workers_combo.winfo_ismapped()
        assert app.workers_combo.winfo_rootx() >= root.winfo_rootx()
        assert app.workers_combo.winfo_rooty() + app.workers_combo.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        app.workers.set(5)
        assert app.settings_panel.workers.get() == 5
        assert app._new_engine().concurrency == 5
        assert utils.read_json(tmp_path / "settings.json")["concurrency"] == 5
        app.settings_panel.workers.set(2)
        assert app.workers_combo.get() == "2"
        app.settings_panel.cancel()
        assert app.workers.get() == 5
        app._set_busy(True)
        assert str(app.workers_combo.cget("state")) == "disabled"
        app._set_busy(False)
        assert str(app.workers_combo.cget("state")) == "readonly"
    finally:
        app._destroy()


@pytest.mark.parametrize("address,active", [
    ("127.0.0.1:8080", False),
    ("proxy.example.invalid:8080", True),
    ("", True),
    (":8080", True),
    ("127.0.0.1:0", True),
    ("127.0.0.1:65536", True),
    ("ftp://127.0.0.1:8080", True),
])
def test_unselected_or_invalid_proxy_never_becomes_active(tmp_path, address, active):
    with patch.object(ProxyCatalog, "start_scheduler"):
        catalog = ProxyCatalog(tmp_path / "proxies.json")
    catalog.add_or_update(ProxyItem(address=address, is_active=active))
    assert catalog.get_active_proxy().id == "builtin-default"
    engine = EverAITTSEngine(proxy_catalog=catalog)
    assert engine.get_current_proxy() == catalog.get_active_proxy().address


def test_rotation_keeps_explicitly_activated_proxy(tmp_path):
    with patch.object(ProxyCatalog, "start_scheduler"):
        catalog = ProxyCatalog(tmp_path / "proxies.json")
    first = ProxyItem(address="127.0.0.1:8080", is_active=True)
    second = ProxyItem(address="127.0.0.1:8081")
    for item in (first, ProxyItem(address="proxy.example.invalid:8080"),
                 ProxyItem(address="127.0.0.1:0"), second):
        catalog.add_or_update(item)
    engine = EverAITTSEngine(proxy_catalog=catalog)
    assert engine.rotate_proxy() == first.address
    assert catalog.get_active_proxy().id == first.id


def test_gui_rejects_enabled_proxy_without_active_selection():
    from src.everai.gui import EverAIGuiApp
    app = MagicMock()
    app.network_busy = app.settings_panel.busy = False
    app.settings_panel.save.return_value = True
    app.store.settings = {"use_proxy": True}
    app.proxy_catalog.get_active_proxy.return_value = None
    with patch("src.everai.gui.EverAITTSEngine") as engine:
        with pytest.raises(ValueError, match="proxy hợp lệ"):
            EverAIGuiApp._new_engine(app)
        engine.assert_not_called()


def test_hard_bound_preserves_long_words_and_unicode():
    text = "Đây là câu " + "x" * 1300 + " kết thúc. " + "Văn bản dài, " * 100
    chunks = utils.split_text_into_chunks(text, 450)
    assert all(0 < len(c) <= 450 for c in chunks)
    assert "".join("".join(chunks).split()) == "".join(text.split())


def test_atomic_store_cwd_and_corrupt_backup(tmp_path, monkeypatch):
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    store = utils.StudioStore(tmp_path)
    store.settings.update(speed=1.3, voice_id="test-voice")
    store.save_settings()
    utils.atomic_json(tmp_path / "draft.json", {"text": "Nội dung chưa xong"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    reopened = utils.StudioStore(tmp_path)
    assert reopened.settings["speed"] == 1.3
    assert reopened.load("draft.json", {})["text"] == "Nội dung chưa xong"
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    recovered = utils.StudioStore(tmp_path)
    assert recovered.settings["speed"] == 1.0
    assert recovered.warnings
    assert list(tmp_path.glob("settings.json.corrupt-*"))


@pytest.mark.parametrize("content", [
    b'{"items": [{"address": "example.invalid:80:user:PRIVATE-PASS"}',
    b"\xff",
    b"[]",
    b"{}",
    b'{"items": {}}',
    b'{"items": [null]}',
    b'{"items": [{"unknown_field": true}]}',
])
def test_corrupt_proxy_catalog_preserves_bytes_before_next_save(tmp_path, content):
    path = tmp_path / "proxies.json"
    path.write_bytes(content)
    messages = []
    catalog = ProxyCatalog(path, lambda message, level: messages.append(message))
    try:
        backups = list(tmp_path.glob("proxies.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == content
        assert path.read_bytes() == content
        assert catalog.warnings
        assert backups[0].name in catalog.warnings[-1]
        assert "PRIVATE-PASS" not in " ".join(catalog.warnings + messages)
        catalog.add_or_update(ProxyItem(address="example.invalid:8080"))
        assert utils.read_json(path)["items"][0]["address"] == "example.invalid:8080"
        assert backups[0].read_bytes() == content
        catalog.warnings.clear()
        catalog.load()
        assert not catalog.warnings
        assert len(list(tmp_path.glob("proxies.json.corrupt-*"))) == 1
    finally:
        catalog.stop_scheduler()


def test_proxy_catalog_backup_failure_stops_loading_without_overwrite(tmp_path):
    path = tmp_path / "proxies.json"
    path.write_bytes(b"{broken")
    with patch("shutil.copyfile", side_effect=PermissionError("backup denied")), patch.object(ProxyCatalog, "start_scheduler") as start:
        with pytest.raises(OSError):
            ProxyCatalog(path)
        start.assert_not_called()
    assert path.read_bytes() == b"{broken"


def test_proxy_catalog_read_failure_is_not_treated_as_empty(tmp_path):
    path = tmp_path / "proxies.json"
    content = b'{"items": []}'
    path.write_bytes(content)
    with patch("src.everai.proxy_manager.open", side_effect=PermissionError("read denied")), patch.object(ProxyCatalog, "start_scheduler") as start:
        with pytest.raises(OSError):
            ProxyCatalog(path)
        start.assert_not_called()
    assert path.read_bytes() == content
    assert not list(tmp_path.glob("proxies.json.corrupt-*"))


def test_gui_shows_corrupt_proxy_backup_warning(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    path = tmp_path / "proxies.json"
    path.write_bytes(b"{broken")
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        backups = list(tmp_path.glob("proxies.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].name in app.status.get()
        assert app.proxy_catalog.get_all() == []
        assert app.settings_panel.save()
        assert backups[0].read_bytes() == b"{broken"
    finally:
        app._destroy()


def test_shared_store_merges_stale_windows_and_respects_delete_and_reset(tmp_path):
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    first, second = utils.StudioStore(tmp_path), utils.StudioStore(tmp_path)
    first.record_proxy_traffic("p", "P", "example.invalid:8080", {"proxy_tx_bytes": 100})
    second.record_proxy_traffic("p", "P", "example.invalid:8080", {"proxy_tx_bytes": 50})
    first.add_result(tmp_path / "one.mp3")
    second.add_result(tmp_path / "two.mp3")
    reopened = utils.StudioStore(tmp_path)
    assert reopened.proxy_traffic_entries()[0]["tx_bytes"] == 150
    assert {entry["path"] for entry in reopened.recent} == {str(tmp_path / "one.mp3"), str(tmp_path / "two.mp3")}
    first.remove_result(tmp_path / "one.mp3")
    second.add_result(tmp_path / "three.mp3")
    assert {entry["path"] for entry in utils.StudioStore(tmp_path).recent} == {str(tmp_path / "two.mp3"), str(tmp_path / "three.mp3")}
    first.reset_proxy_traffic()
    second.record_proxy_traffic("p", "P", "example.invalid:8080", {"proxy_tx_bytes": 7})
    assert utils.StudioStore(tmp_path).proxy_traffic_entries()[0]["tx_bytes"] == 7


def test_shared_store_serializes_updates_from_two_processes(tmp_path):
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    script = """
import sys
from pathlib import Path
from src.everai.utils import StudioStore
root = Path(sys.argv[1])
store = StudioStore(root)
print('ready', flush=True)
sys.stdin.readline()
for i in range(20):
    store.record_proxy_traffic('p', 'P', 'example.invalid:8080', {'proxy_tx_bytes': int(sys.argv[2])})
for i in range(4):
    store.add_result(root / (sys.argv[2] + '-' + str(i) + '.mp3'))
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), str(amount)],
                  cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for amount in (10, 20)]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("start\n")
            process.stdin.flush()
        for process in processes:
            _, errors = process.communicate(timeout=30)
            assert process.returncode == 0, errors
        reopened = utils.StudioStore(tmp_path)
        assert reopened.proxy_traffic_entries()[0]["tx_bytes"] == 600
        assert len(reopened.recent) == 8
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


@pytest.mark.parametrize("on429", [False, True])
def test_quota_retry_respects_proxy_rotation_option(tmp_path, on429):
    item = ProxyItem(address="example.invalid:8080", rotate_url="https://example.invalid/rotate", rotate_on_429=on429)
    catalog = MagicMock()
    catalog.get_active_proxy.return_value = item
    catalog.get_all.return_value = [item]
    catalog.rotate_proxy.return_value = {"ok": True}
    client = MagicMock()
    client.synthesize_one_chunk.side_effect = EverAIQuotaError("mock quota")
    client.get_traffic_stats.return_value = {}
    engine = EverAITTSEngine(proxy_catalog=catalog, randomize_device=False,
                            max_retries_per_chunk=2, retry_delay_sec=0)
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with pytest.raises(EverAIQuotaError):
            engine._process_single_chunk(1, "hello", 1, tmp_path / "chunk.mp3",
                "voice", "everai-v1.6", 1, 1, 100, "128 kbps", "mp3", True)
    assert catalog.rotate_proxy.call_count == int(on429)


def test_saved_proxy_draft_preserves_engine_activation_unless_user_changes_it(tmp_path):
    import tkinter as tk
    from dataclasses import asdict
    from src.everai.gui import EverAIGuiApp
    items = [ProxyItem(id="a", address="a.example.invalid:8080", is_active=True),
             ProxyItem(id="b", address="b.example.invalid:8080")]
    utils.atomic_json(tmp_path / "proxies.json", {"items": [asdict(item) for item in items]})
    utils.atomic_json(tmp_path / "settings.json", {"output_dir": str(tmp_path / "audio"), "use_proxy": True})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        panel = app.settings_panel
        app.proxy_catalog.set_active("b")
        panel.name.set("Edited name")
        panel.address.set(items[0].address)
        panel.selected_id = "a"
        app._new_engine()
        assert app.proxy_catalog.get_active_proxy().id == "b"
        assert app.proxy_catalog.get_by_id("a").name == "Edited name"
        app.next_proxy_refresh = 0
        app._refresh_connection()
        assert [item.id for item in panel.items if item.is_active] == ["b"]
        panel.selected_id = "a"
        panel.activate()
        app.next_proxy_refresh = 0
        app._refresh_connection()
        assert [item.id for item in panel.items if item.is_active] == ["a"]
        assert panel.save()
        assert app.proxy_catalog.get_active_proxy().id == "a"
        app.proxy_catalog.set_active("b")
        app._new_engine()
        assert app.proxy_catalog.get_active_proxy().id == "b"
    finally:
        app._destroy()


def test_saving_unchanged_proxy_keeps_schedule_and_accepts_real_changes(tmp_path):
    import tkinter as tk
    from dataclasses import asdict
    from src.everai.gui import EverAIGuiApp
    item = ProxyItem(address="example.invalid:8080", kind="rotating",
        rotate_url="https://example.invalid/rotate", auto_rotate=True,
        rotate_minutes=5, next_rotate_at=1300, is_active=True)
    utils.atomic_json(tmp_path / "proxies.json", {"items": [asdict(item)]})
    utils.atomic_json(tmp_path / "settings.json", {"output_dir": str(tmp_path / "audio")})
    with patch.object(ProxyCatalog, "start_scheduler"):
        root = tk.Tk()
        root.withdraw()
        app = EverAIGuiApp(root, tmp_path)
    try:
        panel = app.settings_panel
        panel.tree.selection_set(item.id)
        panel.select()
        with patch("src.everai.settings_dialog.time.time", return_value=1100):
            app._new_engine()
        assert app.proxy_catalog.get_active_proxy().next_rotate_at == 1300
        current = app.proxy_catalog.get_active_proxy()
        current.last_rotate_at, current.next_rotate_at = 1200, 1500
        with patch("src.everai.settings_dialog.time.time", return_value=1250):
            app._new_engine()
        assert app.proxy_catalog.get_active_proxy().next_rotate_at == 1500
        assert app.proxy_catalog.get_active_proxy().last_rotate_at == 1200
        panel.minutes.set("10")
        with patch("src.everai.settings_dialog.time.time", return_value=1250):
            assert panel.save()
        assert app.proxy_catalog.get_active_proxy().next_rotate_at == 1850
        panel.auto.set(False)
        assert panel.save()
        assert app.proxy_catalog.get_active_proxy().next_rotate_at is None
    finally:
        app._destroy()


def test_writable_directory_checks_disk_space_before_startup(tmp_path):
    import errno
    target = tmp_path / "probe"
    with patch.object(utils.tempfile, "TemporaryFile") as temporary:
        probe = temporary.return_value.__enter__.return_value
        probe.flush.side_effect = OSError(errno.ENOSPC, "No space left on device")
        with pytest.raises(OSError) as error:
            utils.writable_directory(target)
        assert error.value.errno == errno.ENOSPC
        probe.write.assert_called_once_with(b"\0" * 4096)
        probe.flush.assert_called_once()
    assert utils.writable_directory(target) == target.resolve()
    assert list(target.iterdir()) == []


def test_portable_readonly_does_not_silently_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(utils.sys, "executable", str(tmp_path / "app.exe"))
    (tmp_path / "portable.flag").touch()
    with patch.object(utils, "writable_directory", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            utils.application_data_dir()
    assert utils.application_data_dir() == tmp_path / "EverAIStudio-data"


def test_frozen_empty_catalog_never_copies_developer_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    store = utils.StudioStore(tmp_path)
    assert utils.read_json(store.root / "proxies.json")["items"] == []


def test_secrets_redacted_and_password_salted():
    message = "https://provider/SECRET?key=ABC host:1234:alice:PASS sessionid=COOKIE"
    clean = utils.redact_log(message)
    assert not any(secret in clean for secret in ("SECRET", "ABC", "PASS", "COOKIE"))
    encoded = utils.password_hash("correct horse")
    assert utils.verify_password("correct horse", encoded)
    assert not utils.verify_password("incorrect", encoded)
    assert encoded != utils.password_hash("correct horse")


def test_publish_collision_preserves_existing_file(tmp_path):
    old = tmp_path / "voice.mp3"
    old.write_bytes(b"old audio")
    new = tmp_path / ".partial.mp3"
    new.write_bytes(b"new audio")
    result = utils.publish_audio(new, old)
    assert result.name == "voice (1).mp3"
    assert old.read_bytes() == b"old audio"
    assert result.read_bytes() == b"new audio"
    assert utils.audio_filename("CON") == "Audio CON.mp3"
    assert utils.audio_filename("Hello:/?*") == "Hello.mp3"


def test_single_chunk_rejects_html_instead_of_publishing_fake_mp3(tmp_path):
    if not utils.ensure_ffmpeg():
        pytest.skip("FFmpeg required for integration check")
    source = tmp_path / "chunk.mp3"
    source.write_bytes(b"<html>Service returned an error</html>")
    with pytest.raises(RuntimeError):
        utils.concat_mp3_files([source], tmp_path / "final.mp3")


def test_resume_only_missing_chunks_and_reject_changed_voice(tmp_path):
    job = tmp_path / "job"
    chunks = ["first", "second", "third"]
    client = MagicMock()
    client.synthesize_one_chunk.side_effect = [b"first-audio", EverAIConnectionError("offline")]
    with patch("src.everai.engine.split_text_into_chunks", return_value=chunks), patch("src.everai.engine.EverAIClient", return_value=client), patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat):
        engine = EverAITTSEngine(concurrency=1, max_retries_per_chunk=1, retry_delay_sec=0)
        with pytest.raises(EverAIConnectionError):
            engine.process("full text", tmp_path / "out.mp3", job_dir=job)
        assert utils.read_json(job / "manifest.json")["completed"]["1"]
        with pytest.raises(Exception, match="thay đổi"):
            EverAITTSEngine().process("full text", tmp_path / "out.mp3", speaker_id="changed", job_dir=job)
        client.synthesize_one_chunk.side_effect = lambda text, **_: text.encode() + b"-audio"
        client.synthesize_one_chunk.reset_mock()
        progress = []
        result = EverAITTSEngine(concurrency=3,
            progress_callback=lambda done, total, percent, text: progress.append((done, total, percent))
        ).process("full text", tmp_path / "out.mp3", job_dir=job)
        sent = [call.kwargs["text"] for call in client.synthesize_one_chunk.call_args_list]
        assert sorted(sent) == ["second", "third"]
        assert progress[0] == (1, 3, 30)
        assert [entry[2] for entry in progress] == sorted(entry[2] for entry in progress)
        assert progress[-1] == (3, 3, 100)
        assert result.read_bytes() == b"first-audiosecond-audiothird-audio"
        manifest = utils.read_json(job / "manifest.json")
        assert manifest["status"] == "completed"
        assert "session" not in json.dumps(manifest)
        assert not list(job.glob("*.mp3"))


def test_merge_error_preserves_chunks_then_choose_another_output(tmp_path):
    job = tmp_path / "job"
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"valid-audio"
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with patch("src.everai.engine.concat_mp3_files", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                EverAITTSEngine().process("hello", tmp_path / "out.mp3", job_dir=job)
        assert list(job.glob("*.mp3"))
        client.synthesize_one_chunk.reset_mock()
        with patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat):
            result = EverAITTSEngine().process("hello", tmp_path / "other.mp3", job_dir=job)
        client.synthesize_one_chunk.assert_not_called()
        assert result.name == "other.mp3"


def test_polling_cancel_is_prompt_without_http_call():
    client = EverAIClient()
    client.cancel_event.set()
    try:
        with patch.object(client.session, "get") as get:
            with pytest.raises(EverAICancelled):
                client.poll_for_audio_url("test", interval=30)
            get.assert_not_called()
    finally:
        client.close()


def test_player_reports_real_mci_open_failure(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake")
    player = MiniAudioPlayer()
    player._winmm = MagicMock()
    player._winmm.mciSendStringW.return_value = 123
    assert player.play(audio) is False
    assert not player._is_playing
    assert not player._opened


def test_finished_player_restarts_from_zero():
    player = MiniAudioPlayer()
    player._opened = True
    with patch.object(player, "_send_mci", side_effect=["1000", "1000", ""]) as send:
        assert player.start()
        assert send.call_args.args[0].endswith("from 0")


def test_download_rejects_html_returned_with_http_200():
    from src.everai.client import EverAIError
    client = EverAIClient()
    response = MagicMock(status_code=200, content=b"<html>not audio</html>")
    try:
        with patch.object(client.direct_session, "get", return_value=response):
            with pytest.raises(EverAIError, match="MP3"):
                client.download_audio("https://example.invalid/audio.mp3")
    finally:
        client.close()


def test_gui_draft_settings_cancel_and_failure_fallback(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.settings_dialog import SettingsDialog
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        app.txt_input.insert("1.0", "Nội dung đang soạn")
        app.speed.set(1.2)
        app._save()
        assert app.store.load("draft.json")["text"] == "Nội dung đang soạn"
        dialog = SettingsDialog(root, app)
        dialog.workers.set(7)
        dialog.cancel()
        assert app.store.settings["concurrency"] == 3
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"not audio")
        with patch.object(app.player, "load", return_value=False):
            app._load_result(audio)
        assert app.external_button.winfo_manager() == "pack"
        assert app.last_output_file == audio
        app._clear_text()
        app.txt_input.edit_undo()
        assert app.txt_input.get("1.0", "end-1c") == "Nội dung đang soạn"
    finally:
        app._destroy()
    root = tk.Tk()
    root.withdraw()
    reopened = EverAIGuiApp(root, tmp_path)
    try:
        assert reopened.speed.get() == 1.2
        assert reopened.txt_input.get("1.0", "end-1c") == "Nội dung đang soạn"
    finally:
        reopened._destroy()


def test_admin_authentication_happens_before_window_creation():
    from src.everai.admin_log_dialog import AdminLogDialog
    encoded = utils.password_hash("private-password")
    with patch("src.everai.admin_log_dialog.simpledialog.askstring", return_value="wrong"), patch("tkinter.messagebox.showerror"), patch("src.everai.admin_log_dialog.tk.Toplevel") as window:
        assert AdminLogDialog(None, [], encoded) is None
        window.assert_not_called()


def test_settings_save_keeps_new_proxy_inactive_and_session_out_of_disk(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.settings_dialog import SettingsDialog
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        dialog = SettingsDialog(root, app)
        dialog.output.set(str(tmp_path / "output"))
        dialog.address.set("example.invalid:8080")
        dialog.name.set("My proxy")
        dialog.use_proxy.set(True)
        dialog.session.set("PRIVATE-SESSION")
        dialog.workers.set(2)
        dialog.minutes.set("5")
        dialog.save()
        assert app.proxy_catalog.get_active_proxy().id == "builtin-default"
        saved_proxy = next(p for p in app.proxy_catalog.get_all() if p.name == "My proxy")
        assert not saved_proxy.is_active
        app.proxy_catalog.set_active(saved_proxy.id)
        assert app.proxy_catalog.get_active_proxy().id == saved_proxy.id
        assert app.store.settings["use_proxy"]
        assert app.store.settings["concurrency"] == 2
        assert app.session_id == "PRIVATE-SESSION"
        assert "PRIVATE-SESSION" not in (tmp_path / "settings.json").read_text()
    finally:
        app._destroy()


def test_preview_keeps_result_and_one_click_returns_to_it(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    result, sample = tmp_path / "result.mp3", tmp_path / "sample.mp3"
    result.write_bytes(b"ID3-result")
    sample.write_bytes(b"ID3-sample")
    try:
        with patch.object(app.player, "load", return_value=True), patch.object(app.player, "get_duration_ms", return_value=1000):
            app._load_result(result)
            result_label = app.result_text.get()
            app._load_playback(sample, "Nghe thử giọng")
            assert app.result_text.get() == result_label
            assert app.last_output_file == result
            assert app.return_result_button.winfo_manager() == "pack"
            with patch.object(app, "_play_pause") as play:
                app.return_result_button.invoke()
                play.assert_called_once()
            assert app.playback_file == result
            assert app.return_result_button.winfo_manager() == ""
    finally:
        app._destroy()


def test_preflight_retry_preserves_new_job_instead_of_resuming_old_one(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        app.txt_input.insert("1.0", "A new article")
        app.pending_job = tmp_path / "old-job"
        with patch("src.everai.gui.ensure_ffmpeg", return_value=None):
            app._start_generation(resume=False)
        with patch.object(app, "_start_generation") as start:
            app.retry_button.invoke()
            start.assert_called_once_with(resume=False)
    finally:
        app._destroy()


@pytest.mark.parametrize("preview", [True, False], ids=["preview", "article"])
@pytest.mark.parametrize("error, code", [
    (EverAIConnectionError("offline"), "connection"),
    (EverAIQuotaError("quota"), "quota"),
    (LicenseError("license"), "license"),
    (OSError("disk"), "storage"),
    (utils.AudioAssemblyError("merge"), "audio"),
])
def test_failed_task_retry_preserves_preview_or_article(tmp_path, preview, error, code):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        article = "Bài chính đang soạn, không được tạo khi thử lại mẫu."
        app.txt_input.insert("1.0", article)
        article_job = tmp_path / "jobs" / "pending-article"
        utils.atomic_json(article_job / "manifest.json", dict(
            text=article, config=app._snapshot(), status="interrupted", completed={}))
        balance = app.license_manager.balance()
        app.busy = "preview" if preview else "generation"
        app._set_busy(True)
        with patch.object(app, "_preview_configured") as retry_preview, patch.object(app, "_start_generation") as start_article:
            app._finish(None, error, tmp_path / "samples" / "preview-job" if preview else article_job, preview)
            assert app.last_error_code == code
            app.retry_button.invoke()
            if preview:
                retry_preview.assert_called_once_with()
                start_article.assert_not_called()
            else:
                start_article.assert_called_once_with(resume=True)
                retry_preview.assert_not_called()
        assert app.pending_job == article_job
        assert app.txt_input.get("1.0", "end-1c") == article
        assert app.license_manager.balance() == balance
    finally:
        app._destroy()


def test_asset_digest_failure_preserves_previous_file(tmp_path):
    target = tmp_path / "asset.zip"
    target.write_bytes(b"previous")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.side_effect = [b"tampered", b""]
    response.headers = {"Content-Length": "8"}
    with patch("src.everai.utils.urllib.request.urlopen", return_value=response):
        with pytest.raises(ValueError, match="SHA256"):
            utils.download_file("https://example.invalid/asset.zip", target, "0" * 64)
    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.download"))


@pytest.mark.parametrize("scaling", (1.333333, 1.666667, 2.0))
def test_tabbed_workspace_keeps_editor_and_dock_visible(tmp_path, scaling):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.tk.call("tk", "scaling", scaling)
    app = EverAIGuiApp(root, tmp_path)
    try:
        root.geometry("900x600")
        root.update()
        assert app.advanced.winfo_manager() == "pack"
        assert app.recent_frame.winfo_manager() == "pack"
        assert app.settings_panel.embedded
        assert [app.tabs.tab(tab, "text") for tab in app.tabs.tabs()] == ["Tạo giọng nói", "Quản lý proxy", "Lịch sử"]
        assert app.tabs.select() == str(app.generation_tab)
        assert not app.settings_panel.winfo_viewable()
        assert not app.recent_frame.winfo_viewable()
        assert app.settings_window.state() == "withdrawn"
        assert app.txt_input.winfo_height() >= 80
        assert app.txt_input.winfo_width() >= 300
        assert {"address", "ip", "status", "latency", "schedule"} <= set(app.settings_panel.tree["columns"])
        for widget in (app.txt_input, app.btn_start, app.btn_stop, app.play_button, app.seek):
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty() >= root.winfo_rooty()
            assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        app.txt_input.insert("1.0", "Một hai ba")
        app._update_stats()
        assert "3 từ" in app.stats.get()
        app._set_busy(True)
        assert app.settings_panel.locked
        assert str(app.settings_panel.url_entry.cget("state")) == "disabled"
        app._set_busy(False)
        assert not app.settings_panel.locked
        app.open_proxy()
        root.update()
        assert app.tabs.select() == str(app.proxy_tab)
        assert app.settings_panel.winfo_viewable()
        assert app.btn_start.winfo_ismapped()
        app.tabs.select(app.history_tab)
        root.update()
        assert app.recent_frame.winfo_viewable()
        assert app.activity_log.winfo_viewable()
    finally:
        app._destroy()


def test_settings_window_reopens_draft_and_locks_shared_preferences(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    app = EverAIGuiApp(root, tmp_path)
    try:
        app.txt_input.insert("1.0", "Bản nháp vẫn còn khi chuyển tab")
        panel = app.settings_panel
        panel.workers.set(6)
        app.open_settings()
        root.update()
        assert app.settings_window.state() == "normal"
        assert panel.preferences.winfo_ismapped()
        assert app.license_panel.winfo_toplevel() == app.settings_window
        app._close_settings()
        app.open_settings()
        assert panel.workers.get() == 6
        app._set_busy(True)
        assert not panel.save()
        assert not panel.workers.get() == app.store.settings["concurrency"]
        app._set_busy(False)
        assert panel.save()
        assert app.store.settings["concurrency"] == 6
        panel.workers.set(8)
        panel.cancel()
        assert panel.workers.get() == 6
        app._close_settings()
        app.open_proxy()
        app.tabs.select(app.generation_tab)
        assert app.txt_input.get("1.0", "end-1c") == "Bản nháp vẫn còn khi chuyển tab"
    finally:
        app._destroy()


def test_modern_controls_preserve_progress_navigation_and_disabled_actions(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.ui_theme import StudioButton, ACCENT
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    invoked = MagicMock()
    button = StudioButton(app.settings_panel, text="Keyboard test", command=invoked)
    button.pack()
    try:
        assert app.seek.get() == 0
        app.ui_queue.put(("progress", 55))
        app._poll()
        assert app.progress_bar.get() == pytest.approx(.55)
        app.open_proxy()
        root.update()
        assert app.nav_buttons[str(app.proxy_tab)].cget("fg_color") == ACCENT
        app.settings_panel.set_locked(True)
        button._keyboard_invoke(None)
        invoked.assert_not_called()
        assert button.cget("state") == "disabled"
        app.settings_panel.set_locked(False)
        button._keyboard_invoke(None)
        invoked.assert_called_once()
    finally:
        app._destroy()


def test_inline_settings_apply_to_engine_and_cancel_keeps_saved_values(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        panel = app.settings_panel
        panel.session.set("INLINE-PRIVATE-SESSION")
        panel.workers.set(2)
        panel.random.set(False)
        panel.direct.set(False)
        with patch("src.everai.gui.EverAITTSEngine") as engine:
            app._new_engine()
            assert engine.call_args.kwargs["session_id"] == "INLINE-PRIVATE-SESSION"
            assert engine.call_args.kwargs["concurrency"] == 2
            assert not engine.call_args.kwargs["randomize_device"]
            assert not engine.call_args.kwargs["download_via_direct_ip"]
        assert panel.winfo_exists()
        panel.workers.set(8)
        panel.cancel()
        assert panel.workers.get() == 2
        assert "INLINE-PRIVATE-SESSION" not in (tmp_path / "settings.json").read_text()
        panel.busy = True
        with pytest.raises(ValueError, match="Chờ"):
            app._new_engine()
        panel.busy = False
    finally:
        app._destroy()


def test_proxy_table_keeps_health_and_masks_credentials_without_replacing_edit(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.proxy_manager import ProxyItem
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        panel = app.settings_panel
        item = ProxyItem(address="example.invalid:8080:private-user:private-password", current_ip="192.0.2.1", latency=42, status="Kết nối tốt", kind="rotating", rotate_url="https://example.invalid/rotate", auto_rotate=True, is_active=True)
        panel.items = [item]
        panel.selected_id = item.id
        panel.refresh()
        row = str(panel.tree.item(item.id, "values"))
        assert "192.0.2.1" in row and "42 ms" in row and "5 phút" in row
        assert "private-password" not in row and "private-user" not in row
        panel.name.set("Unsaved edit")
        panel.refresh()
        root.update()
        assert panel.name.get() == "Unsaved edit"
    finally:
        app._destroy()


def test_quick_proxy_check_updates_health_without_applying_unsaved_form(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.proxy_manager import ProxyItem
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        item = ProxyItem(name="Saved proxy", address="example.invalid:8080:user:secret", is_active=True)
        app.proxy_catalog.add_or_update(item)
        app.settings_panel.cancel()
        app.settings_panel.name.set("Unsaved name")
        def check(target, **_):
            target.current_ip, target.latency, target.status = "192.0.2.10", 12, "Kết nối tốt"
            return {"ok": True, "ip": target.current_ip, "latency": 12}
        with patch("src.everai.gui.ProxyCatalog.check_proxy", side_effect=check), patch("src.everai.gui.threading.Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
            app._quick_proxy(False)
            assert app.network_busy and app.settings_panel.locked
            app._poll()
        assert not app.network_busy and not app.settings_panel.locked
        assert app.proxy_catalog.get_active_proxy().current_ip == "192.0.2.10"
        assert app.proxy_catalog.get_active_proxy().name == "Saved proxy"
        assert app.settings_panel.name.get() == "Unsaved name"
        assert "secret" not in app.connection_text.get()
    finally:
        app._destroy()


def test_every_selectable_voice_has_a_verified_offline_demo():
    from src.everai.voices import EVERAI_VOICES
    from src.everai.prepare_voice_samples import verify_samples
    manifest = verify_samples(Path(utils.__file__).parent / "assets" / "voice_samples")
    assert len(manifest["voices"]) == len(EVERAI_VOICES) == 66
    for voice in EVERAI_VOICES:
        assert utils.voice_demo_path(voice["id"]).name == voice["id"] + ".mp3"
        assert "?" not in manifest["voices"][voice["id"]]["source"]


def test_offline_preview_never_starts_engine_and_keeps_result_for_all_voices(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    from src.everai.voices import EVERAI_VOICES
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    result = tmp_path / "result.mp3"
    result.write_bytes(b"ID3result")
    try:
        with patch.object(app, "_new_engine", side_effect=AssertionError("Offline preview used TTS")), patch.object(app.player, "load", return_value=True) as load, patch.object(app.player, "get_duration_ms", return_value=1000), patch.object(app, "_play_pause") as play:
            app._load_result(result)
            label = app.result_text.get()
            for voice in EVERAI_VOICES:
                app._set_voice(voice["id"])
                app._preview()
                assert load.call_args.args[0].name == voice["id"] + ".mp3"
                assert app.result_text.get() == label
                assert app.last_output_file == result
                assert not app.busy and not app.store.recent
            assert play.call_count == len(EVERAI_VOICES)
            app.speed.set(1.5)
            app.pitch.set(.8)
            app._preview()
            assert load.call_args.args[0].name == EVERAI_VOICES[-1]["id"] + ".mp3"
            assert "mẫu gốc" in app.status.get()
    finally:
        app._destroy()


def test_missing_offline_demo_does_not_fall_back_to_network(tmp_path):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.withdraw()
    app = EverAIGuiApp(root, tmp_path)
    try:
        with patch("src.everai.gui.voice_demo_path", return_value=None), patch.object(app, "_new_engine") as engine, patch.object(app, "_play_pause") as play:
            app._preview()
            engine.assert_not_called()
            play.assert_not_called()
            assert "bị hỏng" in app.status.get()
    finally:
        app._destroy()


def test_frozen_demo_resolver_rejects_corruption_and_unsafe_paths(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(utils.sys, "_MEIPASS", str(tmp_path), raising=False)
    samples = tmp_path / "assets" / "voice_samples"
    samples.mkdir(parents=True)
    payload = b"ID3demo"
    (samples / "voice.mp3").write_bytes(payload)
    utils.atomic_json(samples / "manifest.json", {"voices": {"voice": {"file": "voice.mp3", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}})
    assert utils.voice_demo_path("voice") == samples / "voice.mp3"
    assert utils.voice_demo_path("../voice") is None
    (samples / "voice.mp3").write_bytes(b"ID3evil")
    assert utils.voice_demo_path("voice") is None


def test_public_demo_parser_maps_exact_voice_ids_and_rejects_untrusted_hosts():
    from src.everai.prepare_voice_samples import DemoLinks
    parser = DemoLinks()
    parser.feed('''<button id="playButtonvoice_a" onclick="onPlayClick('https://media.everaicdn.com/speakers/demo.mp3?a=1&amp;b=2', 'playButtonvoice_a')"></button>
    <button id="playButtonbad" onclick="onPlayClick('https://evil.invalid/demo.mp3', 'playButtonbad')"></button>
    <button id="playGalleryvoice_a" onclick="onPlayClick('https://media.everaicdn.com/other.mp3', 'playGalleryvoice_a')"></button>''')
    assert parser.links == {"voice_a": "https://media.everaicdn.com/speakers/demo.mp3?a=1&b=2"}


def test_unverified_ffmpeg_archive_is_never_executed(tmp_path, monkeypatch):
    from src.everai import prepare_ffmpeg
    module = tmp_path / "module"
    vendor = module / "vendor" / "ffmpeg"
    utils.atomic_json(vendor / "upstream.json", {"archive_sha256": "0" * 64})
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"tampered archive")
    monkeypatch.setattr(prepare_ffmpeg, "__file__", str(module / "prepare_ffmpeg.py"))
    with patch.object(prepare_ffmpeg.subprocess, "run") as execute:
        with pytest.raises(ValueError, match="SHA256"):
            prepare_ffmpeg.prepare(bad)
        execute.assert_not_called()
    assert not (vendor / "bin" / "ffmpeg.exe").exists()


def test_corrupted_saved_chunk_is_regenerated(tmp_path):
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"audio"
    job = tmp_path / "job"
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with patch("src.everai.engine.concat_mp3_files", side_effect=OSError()):
            with pytest.raises(OSError):
                EverAITTSEngine().process("hello", tmp_path / "audio.mp3", job_dir=job)
        next(job.glob("*.mp3")).write_bytes(b"corrupt")
        client.synthesize_one_chunk.reset_mock()
        with patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat):
            EverAITTSEngine().process("hello", tmp_path / "audio.mp3", job_dir=job)
        client.synthesize_one_chunk.assert_called_once()


def test_stop_waits_for_running_workers_and_keeps_completed_chunks(tmp_path):
    client = MagicMock()
    engine = EverAITTSEngine(concurrency=1, retry_delay_sec=0)
    entered, release = threading.Event(), threading.Event()
    def synthesize(**_):
        entered.set()
        release.wait(3)
        return b"completed audio"
    client.synthesize_one_chunk.side_effect = synthesize
    outcome = []
    def worker():
        try:
            engine.process("hello", tmp_path / "out.mp3", job_dir=tmp_path / "job")
        except Exception as exc:
            outcome.append(exc)
    with patch("src.everai.engine.EverAIClient", return_value=client):
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(3)
        engine.cancel()
        assert thread.is_alive()
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert isinstance(outcome[0], EverAICancelled)
    assert utils.read_json(tmp_path / "job" / "manifest.json")["completed"]["1"]
    assert not (tmp_path / "out.mp3").exists()


@pytest.mark.parametrize("scaling", [1.0, 1.25, 1.5])
def test_main_controls_visible_at_small_size_and_scale(tmp_path, scaling):
    import tkinter as tk
    from src.everai.gui import EverAIGuiApp
    utils.atomic_json(tmp_path / "proxies.json", {"items": []})
    root = tk.Tk()
    root.tk.call("tk", "scaling", scaling * 96 / 72)
    app = EverAIGuiApp(root, tmp_path)
    root.geometry("900x600")
    root.update()
    try:
        for widget in (app.btn_start, app.btn_stop, app.play_button, app.open_button):
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height()
            assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    finally:
        app._destroy()
