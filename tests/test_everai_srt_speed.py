"""Offline concurrency and rotation-barrier tests; no provider calls or credentials."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import json

import pytest

from src.everai.client import EverAICancelled
from src.everai.engine import EverAITTSEngine
from src.everai.tts.engine import MultiProviderTTSEngine
from src.everai.srt_studio.route import RouteCoordinator
from src.everai.srt_studio.runner import SrtRunner
from test_everai_srt_project import offline, project, CONFIG

REAL_EVERAI_RENDER = EverAITTSEngine._process_single_chunk


def test_route_requests_run_in_parallel():
    coordinator = RouteCoordinator()
    barrier = threading.Barrier(3)
    def request():
        with coordinator.request(threading.Event()):
            barrier.wait(timeout=3)
            return coordinator._active
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: request(), range(3)))
    assert max(results) == 3
    assert coordinator._active == 0


def test_concurrent_failures_rotate_once_without_deadlock():
    coordinator = RouteCoordinator()
    barrier = threading.Barrier(3)
    rotations = []
    def rotate(force_rotate=False):
        assert coordinator._active == 0
        rotations.append(force_rotate)
        time.sleep(.02)
        return 'route-after-rotation'
    def request():
        cancel = threading.Event()
        with coordinator.request(cancel):
            barrier.wait(timeout=3)
            result = coordinator.rotate(rotate, lambda: 'route-after-rotation', cancel, True)
            assert coordinator._local.lease['active']
            return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(request) for _ in range(3)]
        assert [f.result(timeout=5) for f in futures] == ['route-after-rotation'] * 3
    assert rotations == [True]
    assert coordinator._active == coordinator._waiting == 0
    assert not coordinator._rotating


def test_rotation_waits_for_healthy_request_and_blocks_new_requests():
    coordinator = RouteCoordinator()
    healthy_entered = threading.Event()
    finish_healthy = threading.Event()
    rotating = threading.Event()
    finish_rotation = threading.Event()
    late_entered = threading.Event()
    def healthy():
        with coordinator.request(threading.Event()):
            healthy_entered.set()
            assert finish_healthy.wait(5)
    def rotation():
        cancel = threading.Event()
        with coordinator.request(cancel):
            def operation(force_rotate=False):
                assert coordinator._active == 0
                rotating.set()
                assert finish_rotation.wait(5)
                return 'new'
            return coordinator.rotate(operation, lambda: 'new', cancel, True)
    def late():
        with coordinator.request(threading.Event()):
            late_entered.set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        healthy_future = pool.submit(healthy)
        assert healthy_entered.wait(3)
        rotation_future = pool.submit(rotation)
        deadline = time.monotonic() + 3
        while not coordinator._waiting and time.monotonic() < deadline:
            time.sleep(.005)
        assert coordinator._waiting
        late_future = pool.submit(late)
        assert not late_entered.wait(.05) and not rotating.is_set()
        finish_healthy.set()
        assert rotating.wait(3)
        assert not late_entered.is_set()
        finish_rotation.set()
        healthy_future.result(timeout=3)
        assert rotation_future.result(timeout=3) == 'new'
        late_future.result(timeout=3)
    assert late_entered.is_set() and coordinator._active == 0


def test_cancel_while_waiting_for_rotation_releases_lease():
    coordinator = RouteCoordinator()
    ready = threading.Event()
    cancel = threading.Event()
    with coordinator.request(threading.Event()):
        def request():
            with coordinator.request(cancel):
                ready.set()
                coordinator.rotate(lambda **kw: pytest.fail('Cancelled rotation must not run'), lambda: None, cancel)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request)
            assert ready.wait(3)
            cancel.set()
            with pytest.raises(EverAICancelled):
                future.result(timeout=3)
        assert coordinator._active == 1
    assert coordinator._active == coordinator._waiting == 0


def test_rotation_failure_does_not_poison_future_requests():
    coordinator = RouteCoordinator()
    cancel = threading.Event()
    with coordinator.request(cancel):
        def fail(**kwargs):
            raise ValueError('offline rotation failure')
        with pytest.raises(ValueError, match='rotation failure'):
            coordinator.rotate(fail, lambda: None, cancel)
        assert coordinator._active == 1
    with coordinator.request(cancel):
        assert coordinator._active == 1
    assert coordinator._active == coordinator._waiting == 0


@pytest.mark.parametrize('provider', ['everai', 'capcut'])
def test_every_provider_uses_three_parallel_cues_and_one_inner_worker(tmp_path, offline, monkeypatch, provider):
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('A', 'B', 'C', 'D', 'E', 'F'))
    config = dict(CONFIG, speaker_id='vi_male_leduc_mb' if provider == 'everai' else provider + '|offline')
    lock = threading.Lock()
    started = threading.Barrier(3)
    active = peak = 0
    original = EverAITTSEngine._process_single_chunk
    def render(self, *args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            # Synchronize workers instead of assuming thread startup takes <120ms.
            # Full PyInstaller builds can otherwise make this assertion flaky.
            started.wait(timeout=30)
            return original(self, *args)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    monkeypatch.setattr(MultiProviderTTSEngine, '_process_single_chunk', render)
    events = []
    runner = SrtRunner(instance, factory, manager, concurrency=3, callback=lambda _, e: events.append(e))
    runner.process(tmp_path / 'out.wav', config)
    assert peak == 3
    assert all(count == 1 for _, _, _, count in calls)
    assert max(e['active'] for e in events) == 3


def test_raw_cues_do_not_decode_again_before_fit(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path)
    from src.everai.srt_studio import audio
    monkeypatch.setattr(audio, 'source_frames', lambda *a: pytest.fail('Redundant pre-fit FFmpeg decode'))
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert all(c['generation']['raw']['frames'] > 0 for c in instance.data['cues'])


def test_render_phase_benchmark_one_vs_three_workers(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    original = EverAITTSEngine._process_single_chunk
    def delayed(self, *args):
        time.sleep(.4)  # Controlled request latency, no network/provider access.
        return original(self, *args)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', delayed)
    times = {}
    for concurrency in (1, 3):
        instance = project(tmp_path, texts=('A', 'B', 'C', 'D', 'E', 'F'))
        phases = {}
        def event(_, details):
            phases.setdefault(details['phase'], time.monotonic())
        SrtRunner(instance, factory, manager, concurrency=concurrency, callback=event).process(
            tmp_path / f'benchmark-{concurrency}.wav', CONFIG)
        times[concurrency] = phases['fitting'] - phases['rendering']
    assert times[3] < times[1] * .85
    print(json.dumps(dict(offline_benchmark=True, cues=6, simulated_request_delay=.4,
                         serial_seconds=round(times[1], 3), parallel_3_seconds=round(times[3], 3),
                         render_speedup=round(times[1] / times[3], 2))))


def test_real_everai_retry_path_shares_rotation_between_cues(tmp_path, offline, monkeypatch):
    from types import SimpleNamespace
    from src.everai import engine as core
    from src.everai.client import EverAIQuotaError
    _, _, manager = offline
    sample = (tmp_path / 'source.mp3').read_bytes()
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    attempts, rotations = {}, []
    healthy = [False]
    class Catalog:
        active = SimpleNamespace(id='offline-proxy', name='Offline route',
            address='http://127.0.0.1:9999', rotate_url='offline-only', rotate_on_429=True)
        def get_active_proxy(self):
            return self.active
        def rotate_proxy(self, item, **kwargs):
            assert not healthy[0]
            rotations.append(1)
            return dict(ok=True, ip='127.0.0.1')
    class Client:
        csrf_token = None
        def __init__(self, **kwargs):
            pass
        def synthesize_one_chunk(self, text, **kwargs):
            with lock:
                attempts[text] = attempts.get(text, 0) + 1
                number = attempts[text]
                if text == 'C':
                    healthy[0] = True
            if number == 1:
                barrier.wait(timeout=30)
            if number == 1 and text in ('A', 'B'):
                raise EverAIQuotaError('offline retry')
            if text == 'C':
                time.sleep(.1)
                healthy[0] = False
            return sample
        def close(self):
            pass
    monkeypatch.setattr(core, 'EverAIClient', Client)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', REAL_EVERAI_RENDER)
    catalog = Catalog()
    def factory(config, concurrency):
        return EverAITTSEngine(license_manager=manager, concurrency=concurrency,
            temp_dir=tmp_path / 'chunks', proxy_catalog=catalog, randomize_device=False,
            max_retries_per_chunk=2, retry_delay_sec=0)
    instance = project(tmp_path, texts=('A', 'B', 'C'))
    result = SrtRunner(instance, factory, manager, concurrency=3).process(tmp_path / 'retry.wav', CONFIG)
    assert result.is_file()
    assert attempts == dict(A=2, B=2, C=1)
    assert rotations == [1]
    assert manager.balance()['held'] == 0
