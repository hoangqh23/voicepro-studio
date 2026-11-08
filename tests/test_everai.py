import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.everai.voices import (
    EVERAI_VOICES,
    DEFAULT_VOICE_ID,
    get_voice_by_id,
    get_voice_by_display,
    get_display_names,
)
from src.everai.client import EverAIClient, EverAIError, EverAIQuotaError
from src.everai.engine import EverAITTSEngine

pytestmark = pytest.mark.usefixtures("funded_license")


def test_voices_catalog():
    assert len(EVERAI_VOICES) == 66
    assert DEFAULT_VOICE_ID == "vi_male_leduc_mb"

    voice = get_voice_by_id("vi_male_leduc_mb")
    assert voice is not None
    assert voice["name"] == "Lê Đức"
    assert "Miền Bắc" in voice["region"]

    names = get_display_names()
    assert len(names) == 66
    assert any("Lê Đức" in n for n in names)

    by_disp = get_voice_by_display(names[0])
    assert by_disp is not None


def test_client_validation_empty_text():
    client = EverAIClient()
    with pytest.raises(EverAIError, match="không được để trống"):
        client.create_request("")


def test_client_validation_text_too_long():
    client = EverAIClient()
    with pytest.raises(EverAIError, match="vượt quá giới hạn 500"):
        client.create_request("A" * 501)


def test_client_handles_429_quota():
    client = EverAIClient()
    client.csrf_token = "fake-csrf"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": 0,
        "error_code": 429,
        "error_message": "Bạn đã vượt quá giới hạn chuyển đổi.",
    }

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(EverAIQuotaError, match="giới hạn"):
            client.create_request("Chào bạn")


def test_client_create_and_poll_success():
    client = EverAIClient()
    client.csrf_token = "fake-csrf"

    create_resp = MagicMock()
    create_resp.status_code = 200
    create_resp.json.return_value = {
        "status": 1,
        "result": {"request_id": "req-12345"},
    }

    poll_resp = MagicMock()
    poll_resp.status_code = 200
    poll_resp.json.return_value = {
        "status": 1,
        "result": {
            "status": "hoàn thành",
            "progress": 100,
            "url": "https://example.com/audio.mp3",
        },
    }

    with patch.object(client.session, "post", return_value=create_resp):
        req_id = client.create_request("Chào bạn")
        assert req_id == "req-12345"

    with patch.object(client.session, "get", return_value=poll_resp):
        with patch("time.sleep", return_value=None):
            url = client.poll_for_audio_url(req_id, max_attempts=2)
            assert url == "https://example.com/audio.mp3"


def test_engine_text_chunking():
    engine = EverAITTSEngine(max_chunk_chars=100)
    # Long text with multiple sentences
    long_text = "Câu thứ nhất rất là dài để kiểm tra việc cắt câu. " * 5
    chunks = engine.split_text(long_text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 100


def test_engine_proxy_rotation():
    proxies = ["proxy1:1000", "proxy2:2000", "proxy3:3000"]
    engine = EverAITTSEngine(proxies=proxies)

    assert engine.get_current_proxy() == "proxy1:1000"
    p2 = engine.rotate_proxy()
    assert p2 == "proxy2:2000"
    assert engine.rotate_proxy() == "proxy3:3000"
    assert engine.rotate_proxy() == "proxy1:1000"


def test_catalog_chunks_and_retry_never_select_inactive_proxies(tmp_path):
    from src.everai.proxy_manager import ProxyCatalog, ProxyItem
    catalog = ProxyCatalog(tmp_path / 'proxies.json')
    a = ProxyItem(name='inactive', address='127.0.0.1:8081')
    b = ProxyItem(name='chosen', address='127.0.0.1:8082')
    catalog.add_or_update(a)
    catalog.add_or_update(b)
    engine = EverAITTSEngine(proxy_catalog=catalog)
    assert all(engine.get_proxy_for_chunk(i) == catalog.get_active_proxy().address for i in range(1, 5))
    assert catalog.get_active_proxy().id == 'builtin-default'
    catalog.set_active(b.id)
    assert all(engine.get_proxy_for_chunk(i) == b.address for i in range(1, 5))
    assert engine.rotate_proxy(force_rotate=True) == b.address
    assert catalog.get_active_proxy().id == b.id


def test_client_proxy_quad_format():
    # User's format: IP:PORT:USER:PASS
    client = EverAIClient(proxy="160.250.47.204:28018:HyjvAb:dPaJvx")
    assert "http" in client.session.proxies
    expected_url = "http://HyjvAb:dPaJvx@160.250.47.204:28018"
    assert client.session.proxies["http"] == expected_url
    assert client.session.proxies["https"] == expected_url


def test_proxy_catalog_crud(tmp_path):
    from src.everai.proxy_manager import ProxyCatalog, ProxyItem

    catalog_file = tmp_path / "test_catalog.json"
    catalog = ProxyCatalog(storage_path=catalog_file)

    assert catalog.get_all() == []
    assert catalog.get_active_proxy().id == "builtin-default"

    # Add a rotating proxy
    rot_item = ProxyItem(
        name="Test Rotating Proxy",
        kind="rotating",
        address="160.250.47.204:28018:HyjvAb:dPaJvx",
        rotate_url="https://api.example.com/rotate",
        rotate_minutes=10,
        auto_rotate=True,
        rotate_on_429=True,
    )
    catalog.add_or_update(rot_item)

    assert len(catalog.get_all()) == 1
    assert catalog.get_active_proxy().id == "builtin-default"
    assert "builtin-default" not in catalog_file.read_text(encoding="utf-8")
    found = catalog.get_by_id(rot_item.id)
    assert found is not None
    assert found.rotate_url == "https://api.example.com/rotate"

    catalog.set_active(rot_item.id)
    active = catalog.get_active_proxy()
    assert active.id == rot_item.id

    # Test delete
    assert catalog.delete(rot_item.id) is True
    assert catalog.get_by_id(rot_item.id) is None
    assert catalog.get_active_proxy().id == "builtin-default"
    assert catalog.get_all() == []


def test_device_profile_generation():
    from src.everai.device_profiles import get_random_device_profile, DeviceProfile

    profile1 = get_random_device_profile()
    assert isinstance(profile1, DeviceProfile)
    assert "Mozilla/5.0" in profile1.user_agent
    assert profile1.sec_ch_ua != ""
    assert profile1.sec_ch_ua_mobile == "?0"
    assert profile1.sec_ch_ua_platform in ['"Windows"', '"macOS"']

    headers = profile1.to_headers()
    assert headers["User-Agent"] == profile1.user_agent
    assert headers["sec-ch-ua"] == profile1.sec_ch_ua
    assert headers["sec-ch-ua-platform"] == profile1.sec_ch_ua_platform
    assert "vi" in headers["Accept-Language"]

    # Verify platform coherence
    if profile1.sec_ch_ua_platform == '"Windows"':
        assert "Windows NT" in profile1.user_agent
    elif profile1.sec_ch_ua_platform == '"macOS"':
        assert "Macintosh" in profile1.user_agent


def test_client_applies_device_profile():
    from src.everai.device_profiles import get_random_device_profile

    profile = get_random_device_profile()
    client = EverAIClient(device_profile=profile)

    assert client.session.headers["User-Agent"] == profile.user_agent
    assert client.session.headers["sec-ch-ua"] == profile.sec_ch_ua
    assert client.session.headers["sec-ch-ua-platform"] == profile.sec_ch_ua_platform
    assert client.session.headers["Accept-Language"] == profile.accept_language


def test_engine_randomize_device_mode(tmp_path):
    engine = EverAITTSEngine(temp_dir=tmp_path, randomize_device=True)
    assert engine.randomize_device is True

    with patch("src.everai.engine.EverAIClient") as MockClient:
        instance = MockClient.return_value
        instance.synthesize_one_chunk.return_value = b"ID3\x03\x00\x00\x00"

        with patch("src.everai.engine.concat_mp3_files") as mock_concat:
            mock_concat.return_value = tmp_path / "final.mp3"
            (tmp_path / "final.mp3").write_bytes(b"ID3fake")

            out_file = tmp_path / "out.mp3"
            engine.process("Đoạn một kiểm tra thiết bị.", output_file=out_file)

        assert MockClient.call_count >= 1
        for call_args in MockClient.call_args_list:
            kwargs = call_args.kwargs
            assert "device_profile" in kwargs
            assert kwargs["device_profile"] is not None


def test_client_model_id_payload():
    client = EverAIClient()
    client.csrf_token = "fake-csrf-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": 1, "result": {"request_id": "req-model-test"}}

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        # Default model (v1.6)
        client.create_request("Kiểm tra model v1.6")
        assert mock_post.call_count == 1
        payload1 = mock_post.call_args.kwargs["json"]
        assert payload1["model_id"] == "everai-v1.6"

        # Explicit model v1.5
        client.create_request("Kiểm tra model v1.5", model_id="everai-v1.5")
        assert mock_post.call_count == 2
        payload2 = mock_post.call_args.kwargs["json"]
        assert payload2["model_id"] == "everai-v1.5"


def test_engine_process_forwards_model_id(tmp_path):
    engine = EverAITTSEngine(temp_dir=tmp_path)

    with patch("src.everai.engine.EverAIClient") as MockClient:
        instance = MockClient.return_value
        instance.synthesize_one_chunk.return_value = b"ID3\x03\x00\x00\x00"

        with patch("src.everai.engine.concat_mp3_files") as mock_concat:
            mock_concat.return_value = tmp_path / "final.mp3"
            (tmp_path / "final.mp3").write_bytes(b"ID3fake")

            out_file = tmp_path / "out.mp3"
            engine.process("Kiểm tra truyền model_id.", output_file=out_file, model_id="everai-v1.5")

        assert instance.synthesize_one_chunk.call_count >= 1
        for call_args in instance.synthesize_one_chunk.call_args_list:
            assert call_args.kwargs.get("model_id") == "everai-v1.5"


def test_proxy_check_retry_on_timeout(tmp_path):
    from src.everai.proxy_manager import ProxyCatalog, ProxyItem
    import requests

    catalog = ProxyCatalog(storage_path=tmp_path / "proxies.json")
    item = ProxyItem(
        name="Test Timeout Proxy",
        address="160.250.47.204:28018",
    )

    # First attempt raises Read timed out, second attempt succeeds
    mock_success = MagicMock()
    mock_success.status_code = 200
    mock_success.json.return_value = {"ip": "103.1.2.3"}

    with patch("requests.get", side_effect=[
        requests.exceptions.ReadTimeout("HTTPSConnectionPool: Read timed out. (read timeout=10)"),
        mock_success,
    ]):
        with patch("time.sleep", return_value=None):
            res = catalog.check_proxy(item, max_retries=2, retry_delay_sec=0.1, auto_rotate_on_fail=False)
            assert res["ok"] is True
            assert res["ip"] == "103.1.2.3"
            assert item.status == "Kết nối tốt"


def test_engine_proxy_timeout_no_machine_ip_fallback(tmp_path):
    from src.everai.client import EverAIConnectionError

    proxies = ["proxy1:1000", "proxy2:2000"]
    engine = EverAITTSEngine(proxies=proxies, temp_dir=tmp_path, max_retries_per_chunk=3)

    client_proxies_used = []

    def mock_client_init(*args, **kwargs):
        client_proxies_used.append(kwargs.get("proxy"))
        mock = MagicMock()
        # First call fails with Read timed out, second call succeeds
        if len(client_proxies_used) == 1:
            mock.synthesize_one_chunk.side_effect = EverAIConnectionError("Read timed out")
        else:
            mock.synthesize_one_chunk.return_value = b"ID3\x03\x00\x00\x00"
        return mock

    with patch("src.everai.engine.EverAIClient", side_effect=mock_client_init):
        with patch("src.everai.engine.concat_mp3_files") as mock_concat:
            mock_concat.return_value = tmp_path / "final.mp3"
            (tmp_path / "final.mp3").write_bytes(b"ID3fake")
            with patch("time.sleep", return_value=None):
                engine.process("Kiểm tra không fallback về IP máy.", output_file=tmp_path / "out.mp3")

    # Verify that proxy was rotated and NEVER fell back to None (machine IP)
    assert len(client_proxies_used) >= 2
    assert None not in client_proxies_used
    assert client_proxies_used[0] == "proxy1:1000"
    assert client_proxies_used[1] == "proxy2:2000"


def test_device_profile_impersonate_targets():
    from src.everai.device_profiles import DEVICE_PRESETS, get_random_device_profile

    for preset in DEVICE_PRESETS:
        assert "impersonate" in preset
        assert preset["impersonate"].startswith("chrome") or preset["impersonate"].startswith("edge") or preset["impersonate"].startswith("safari")

    prof = get_random_device_profile()
    assert hasattr(prof, "impersonate")
    assert prof.impersonate in ["chrome131", "chrome124", "edge131", "safari17_0"]


def test_client_tls_impersonation_enabled():
    from src.everai.device_profiles import get_random_device_profile

    prof = get_random_device_profile()
    client = EverAIClient(device_profile=prof)

    assert client.is_tls_impersonated is True
    assert client.impersonate_target == prof.impersonate
    assert hasattr(client.session, "hooks")


def test_download_audio_via_direct_ip():
    client = EverAIClient(proxy="160.250.47.204:28018", download_via_direct_ip=True)

    # Session has proxy
    assert "http" in client.session.proxies
    # Direct session has NO proxy
    assert getattr(client.direct_session, "proxies", {}) == {}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"ID3\x03fake-audio"

    # Test download uses direct_session
    with patch.object(client.direct_session, "get", return_value=mock_resp) as mock_direct_get:
        audio = client.download_audio("https://r2.example.com/audio.mp3", use_direct_ip=True)
        assert audio == b"ID3\x03fake-audio"
        assert mock_direct_get.call_count == 1

    # When use_direct_ip=False, it uses client.session
    with patch.object(client.session, "get", return_value=mock_resp) as mock_proxy_get:
        audio2 = client.download_audio("https://r2.example.com/audio.mp3", use_direct_ip=False)
        assert audio2 == b"ID3\x03fake-audio"
        assert mock_proxy_get.call_count == 1


def test_format_bytes():
    from src.everai.utils import format_bytes

    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1536) == "1.50 KB"
    assert format_bytes(1024 * 1024) == "1.00 MB"
    assert format_bytes(1024 * 1024 * 1024) == "1.00 GB"


def test_client_and_engine_traffic_stats(tmp_path):
    from src.everai.client import EverAIClient
    from src.everai.engine import EverAITTSEngine

    client = EverAIClient(proxy="160.250.47.204:28018", download_via_direct_ip=True)

    # Initial stats should be 0
    stats = client.get_traffic_stats()
    assert stats["proxy_tx_bytes"] == 0
    assert stats["proxy_rx_bytes"] == 0
    assert stats["proxy_total_bytes"] == 0
    assert stats["direct_tx_bytes"] == 0
    assert stats["direct_rx_bytes"] == 0
    assert stats["direct_total_bytes"] == 0
    assert stats["total_bytes"] == 0

    # Simulate a proxy request
    mock_resp_proxy = MagicMock()
    mock_resp_proxy.status_code = 200
    mock_resp_proxy.request_size = 500
    mock_resp_proxy.response_size = 1200
    mock_resp_proxy.json.return_value = {"ok": True}

    base_session_cls = client.session.__class__.__bases__[0]
    with patch.object(base_session_cls, "request", return_value=mock_resp_proxy):
        client.session.get("https://example.com/api")

    stats = client.get_traffic_stats()
    assert stats["proxy_tx_bytes"] == 500
    assert stats["proxy_rx_bytes"] == 1200
    assert stats["proxy_total_bytes"] == 1700
    assert stats["direct_total_bytes"] == 0
    assert stats["total_bytes"] == 1700

    # Simulate a direct machine IP request
    mock_resp_direct = MagicMock()
    mock_resp_direct.status_code = 200
    mock_resp_direct.request_size = 300
    mock_resp_direct.response_size = 500000
    mock_resp_direct.content = b"ID3" * 1000

    with patch.object(base_session_cls, "request", return_value=mock_resp_direct):
        client.direct_session.get("https://r2.example.com/audio.mp3")

    stats = client.get_traffic_stats()
    assert stats["proxy_total_bytes"] == 1700
    assert stats["direct_tx_bytes"] == 300
    assert stats["direct_rx_bytes"] == 500000
    assert stats["direct_total_bytes"] == 500300
    assert stats["total_bytes"] == 1700 + 500300

    # Test engine accumulation
    engine = EverAITTSEngine(proxies=["proxy1:1000"], temp_dir=tmp_path)
    engine.total_proxy_tx_bytes = 1000
    engine.total_proxy_rx_bytes = 4000
    engine.total_direct_tx_bytes = 500
    engine.total_direct_rx_bytes = 900000

    e_stats = engine.get_traffic_stats()
    assert e_stats["proxy_tx_bytes"] == 1000
    assert e_stats["proxy_rx_bytes"] == 4000
    assert e_stats["proxy_total_bytes"] == 5000
    assert e_stats["direct_tx_bytes"] == 500
    assert e_stats["direct_rx_bytes"] == 900000
    assert e_stats["direct_total_bytes"] == 900500
    assert e_stats["total_bytes"] == 5000 + 900500


def test_traffic_is_attributed_per_proxy_and_persisted(tmp_path):
    from src.everai.engine import EverAITTSEngine
    from src.everai.utils import StudioStore

    engine = EverAITTSEngine()
    engine._accumulate_client_traffic(
        {"proxy_id": "proxy-a", "name": "A", "address": "10.0.0.1:8080"},
        {"proxy_tx_bytes": 1234, "proxy_rx_bytes": 5678, "proxy_request_count": 3,
         "direct_tx_bytes": 9, "direct_rx_bytes": 10, "measurement": "curl-http-transfer"},
    )
    engine._accumulate_client_traffic(
        {"proxy_id": "proxy-b", "name": "B", "address": "10.0.0.2:8080"},
        {"proxy_tx_bytes": 100, "proxy_rx_bytes": 200, "proxy_request_count": 1,
         "measurement": "curl-http-transfer"},
    )
    entries = {entry["proxy_id"]: entry for entry in engine.get_proxy_traffic_stats()}
    assert entries["proxy-a"]["tx_bytes"] == 1234
    assert entries["proxy-a"]["rx_bytes"] == 5678
    assert entries["proxy-a"]["request_count"] == 3
    assert entries["proxy-b"]["tx_bytes"] == 100

    store = StudioStore(tmp_path)
    store.record_proxy_traffic("proxy-a", "A", "10.0.0.1:8080", {
        "proxy_tx_bytes": 1234, "proxy_rx_bytes": 5678, "proxy_request_count": 3,
        "measurement": "curl-http-transfer",
    })
    store.record_proxy_traffic("proxy-a", "A", "10.0.0.1:8080", {
        "proxy_tx_bytes": 1, "proxy_rx_bytes": 2, "proxy_request_count": 1,
        "measurement": "curl-http-transfer",
    })
    reopened = StudioStore(tmp_path)
    entry = reopened.proxy_traffic_entries()[0]
    assert entry["tx_bytes"] == 1235
    assert entry["rx_bytes"] == 5680
    assert entry["request_count"] == 4
    assert "10.0.0.1:8080" in (tmp_path / "proxy_traffic.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("workers", [1, 3, 8])
def test_engine_concurrency_and_strict_ordering(tmp_path, workers):
    from src.everai.engine import EverAITTSEngine

    progress = []
    engine = EverAITTSEngine(temp_dir=tmp_path, concurrency=workers,
                            progress_callback=lambda done, total, percent, text: progress.append((done, total, percent)))
    assert engine.concurrency == workers

    # Generate 5 chunks
    chunks_text = [
        "Phân đoạn số một.",
        "Phân đoạn số hai.",
        "Phân đoạn số ba.",
        "Phân đoạn số bốn.",
        "Phân đoạn số năm.",
    ]

    # Deliberately introduce inverse sleep times so chunks finish out of order
    # Chunk 1 finishes last (0.15s), Chunk 5 finishes first (0.01s)
    sleep_map = {
        "Phân đoạn số một.": 0.15,
        "Phân đoạn số hai.": 0.10,
        "Phân đoạn số ba.": 0.07,
        "Phân đoạn số bốn.": 0.03,
        "Phân đoạn số năm.": 0.01,
    }

    completion_order = []

    def mock_synthesize(text, **kwargs):
        import time
        s = sleep_map.get(text, 0.01)
        time.sleep(s)
        completion_order.append(text)
        return f"AUDIO_DATA_{text}".encode("utf-8")

    with patch("src.everai.engine.split_text_into_chunks", return_value=chunks_text):
        with patch("src.everai.engine.EverAIClient") as MockClient:
            instance = MockClient.return_value
            instance.synthesize_one_chunk.side_effect = mock_synthesize
            instance.get_traffic_stats.return_value = {
                "proxy_tx_bytes": 100,
                "proxy_rx_bytes": 200,
                "proxy_total_bytes": 300,
                "direct_tx_bytes": 0,
                "direct_rx_bytes": 0,
                "direct_total_bytes": 0,
                "total_bytes": 300,
            }

            with patch("src.everai.engine.concat_mp3_files") as mock_concat:
                mock_concat.return_value = tmp_path / "final.mp3"
                (tmp_path / "final.mp3").write_bytes(b"FINAL_AUDIO")

                out_file = tmp_path / "output_test.mp3"
                res = engine.process("Full input text here", output_file=out_file)
                assert res == tmp_path / "final.mp3"

                # Verify chunks finished out of order in parallel
                if workers > 1:
                    assert completion_order != chunks_text
                else:
                    assert completion_order == chunks_text
                assert [p[0] for p in progress[:6]] == list(range(6))
                assert [p[2] for p in progress] == sorted(p[2] for p in progress)
                assert progress[-1] == (5, 5, 100)

                # Verify concat_mp3_files received chunk_files in STRICT 1..5 order!
                assert mock_concat.call_count == 1
                stitched_files = mock_concat.call_args[0][0]
                assert len(stitched_files) == 5

                for idx, path in enumerate(stitched_files, start=1):
                    assert f"chunk_{idx:03d}_" in path.name
                    # Verify content matches original chunk text
                    content = path.read_bytes()
                    expected_text = chunks_text[idx - 1]
                    assert content == f"AUDIO_DATA_{expected_text}".encode("utf-8")






