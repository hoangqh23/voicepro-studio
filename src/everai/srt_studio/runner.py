"""Coordinate durable per-cue TTS jobs, then fit and export without network."""
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import threading
import time

from . import audio
from .model import canonical_config, chunks_for, identifier, speech_text, validate_cues
from .store import project_lock
from .route import RouteCoordinator
try:
    from ..client import EverAICancelled
    from ..license_manager import LicenseError
    from ..tts.service import split_voice_id
    from ..utils import atomic_json, read_json
except ImportError:
    from client import EverAICancelled
    from license_manager import LicenseError
    from tts.service import split_voice_id
    from utils import atomic_json, read_json


class SrtRunner:
    def __init__(self, project, engine_factory, license_manager, concurrency=3, callback=None):
        self.project = project
        self.engine_factory = engine_factory
        self.license_manager = license_manager
        self.concurrency = max(1, min(8, int(concurrency)))
        self.callback = callback or (lambda event, details: None)
        self.cancel_event = threading.Event()
        self._lock = threading.RLock()
        self._engines = set()
        self._traffic = []
        self._last_checkpoint = 0.
        self._route = RouteCoordinator()
        self._completed_cues = 0
        self._total_cues = 0

    def checkpoint(self):
        # Child manifests/assets are durable individually; throttle large parent snapshots.
        with self._lock:
            if time.monotonic() - self._last_checkpoint >= 1:
                self.project.save()
                self._last_checkpoint = time.monotonic()

    def cancel(self):
        with self._lock:
            self.cancel_event.set()
            for engine in self._engines:
                engine.cancel()

    def get_proxy_traffic_stats(self):
        return list(self._traffic)

    def emit(self, phase, completed=0, total=0, cue=None, **details):
        with self._lock:
            active = len(self._engines)
        self.callback('srt', dict(phase=phase, completed=completed, total=total,
                                 cue_id=cue['id'] if cue else None, active=active,
                                 workers=self.concurrency, **details))

    def _raw(self, cue):
        """Validate recorded raw or reconcile child publish before parent metadata."""
        generation = cue.get('generation')
        if not generation:
            return None
        root = self.project.generation_root(cue)
        metadata = generation.get('raw')
        path = self.raw_path(cue)
        if metadata:
            if not path.is_file() or audio.file_hash(path) != metadata.get('sha256'):
                raise ValueError(f'Cue {cue["number"]}: audio gốc bị mất/hỏng. Chọn Tạo lại cue (có tính phí).')
            return path
        manifest_path = self.project.path(root / 'job/manifest.json')
        manifest = read_json(manifest_path) if manifest_path.is_file() else None
        if manifest and manifest.get('status') == 'completed':
            if (manifest.get('text') != generation['text'] or manifest.get('config') != generation['config']):
                raise ValueError('Manifest cue không khớp nội dung/cấu hình.')
            result = self.project.path(str(manifest.get('result', '')))
            if not result.is_relative_to(root) or not result.is_file():
                raise ValueError(f'Cue {cue["number"]}: mất audio đã tạo. Chọn Tạo lại cue (có tính phí).')
            if result != path:
                # Child publish collision recovery is explicit; keep the canonical result path.
                generation['raw_file'] = result.relative_to(self.project.root).as_posix()
                path = result
            frames = audio.source_frames(path, self.cancel_event)
            generation['raw'] = dict(sha256=audio.file_hash(path), frames=frames)
            return path
        return None

    def raw_path(self, cue):
        generation = cue['generation']
        if generation.get('raw_file'):
            return self.project.path(generation['raw_file'])
        return self.project.path(self.project.generation_root(cue) / 'raw.mp3')

    def estimate(self, config, cue_id=None):
        """No writes/network. Snapshot estimate; actual reservations stay in each child."""
        config = canonical_config(config)
        provider, _ = split_voice_id(config['speaker_id'])
        total = cost = count = 0
        needs_credentials = False
        cues = self.project.data['cues'] if cue_id is None else [self.project.cue(cue_id)]
        for cue in cues:
            audio.check_cancel(self.cancel_event)
            chunks = chunks_for(cue['text'], provider)
            count += len(chunks)
            total += sum(map(len, chunks))
            generation = cue.get('generation')
            same = generation and generation['config'] == config and generation['text'] == speech_text(cue['text'])
            if same and self._raw(cue):
                continue
            paid = set()
            valid_paid = set()
            if same and self.license_manager is not None:
                import hashlib
                job = self.project.generation_root(cue) / 'job'
                job_id = hashlib.sha256(str(job.resolve()).encode()).hexdigest()
                rows = self.license_manager.recovered_chunks(job_id)
                paid = {row[0] for row in rows}
                manifest = read_json(job / 'manifest.json') if (job / 'manifest.json').is_file() else {}
                if not isinstance(manifest, dict):
                    raise ValueError('Manifest chunk SRT không hợp lệ; dữ liệu cũ được giữ lại.')
                fingerprint = manifest.get('fingerprint', '')
                for index, saved, expected_hash in rows:
                    expected_path = job / f'chunk_{index:03d}_{fingerprint[:8]}.mp3'
                    candidate = self.project.path(saved)
                    if candidate == expected_path and candidate.is_file() and audio.file_hash(candidate) == expected_hash:
                        valid_paid.add(index)
            needs_credentials |= any(i not in valid_paid for i in range(1, len(chunks) + 1))
            cost += sum(len(chunk) for i, chunk in enumerate(chunks, 1) if i not in paid)
        return dict(cues=len(cues), chunks=count, characters=total,
                    remaining=cost, needs_render=needs_credentials)

    def _render(self, cue, config, inner_concurrency):
        audio.check_cancel(self.cancel_event)
        engine = self.engine_factory(config, inner_concurrency)
        if split_voice_id(config['speaker_id'])[0] == 'everai':
            self._route.bind(engine)
        with self._lock:
            if self.cancel_event.is_set():
                engine.cancel()
                audio.check_cancel(self.cancel_event)
            self._engines.add(engine)
        self.emit('rendering', self._completed_cues, self._total_cues, cue)
        root = self.project.generation_root(cue)
        try:
            root.mkdir(parents=True, exist_ok=True)
            job = self.project.path(root / 'job')
            result = engine.process(cue['generation']['text'], root / 'raw.mp3', **config,
                                    job_dir=job)
            with self._lock:
                # Core already validates/remuxes the MP3. Decode it just once, at fit.
                cue['generation']['raw'] = dict(sha256=audio.file_hash(result), frames=None)
                cue['generation']['raw_file'] = Path(result).relative_to(self.project.root).as_posix()
                cue['state'] = 'raw_ready'
                cue.pop('error', None)
                atomic_json(root / 'raw-metadata.json', cue['generation']['raw'])
                self.checkpoint()
        except Exception as error:
            with self._lock:
                cue['state'] = 'cancelled' if isinstance(error, EverAICancelled) else 'error'
                cue['error'] = 'Tạo giọng chưa hoàn tất. Audio/chunk đã tạo được giữ lại.'
                self.project.save()
            raise
        finally:
            with self._lock:
                self._engines.discard(engine)
                self._traffic.extend(engine.get_proxy_traffic_stats())

    def process(self, destination, config, *, policy='bounded', max_tempo=1.35,
                output_format='wav', render_only=None):
        with project_lock(self.project.root):
            self.project.reload()
            validate_cues(self.project.data['cues'])
            config = canonical_config(config)
            self.project.validate_options(policy, max_tempo, output_format)
            audio.check_cancel(self.cancel_event)
            self.project.configure(config, policy, max_tempo, output_format)
            destination = Path(destination).resolve().with_suffix('.' + output_format)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if render_only is None:
                audio.check_space(destination.parent, max(c['end_ms'] for c in self.project.data['cues']) * 48,
                                  output_format)
            else:
                selected = self.project.cue(render_only)
                audio.check_space(self.project.root, (selected['end_ms'] - selected['start_ms']) * 48, 'wav')
            try:
                estimate = self.estimate(config, cue_id=render_only)
                if estimate['needs_render']:
                    if self.license_manager is None:
                        raise LicenseError('Cần license để tạo giọng cho cue SRT.')
                    self.license_manager.ensure_active()
                    if estimate['remaining'] > self.license_manager.balance()['available']:
                        raise LicenseError(f'Không đủ ký tự: cần {estimate["remaining"]:,} ký tự còn thiếu cho SRT.')
                cues = self.project.data['cues']
                if render_only is not None:
                    cues = [self.project.cue(render_only)]
                pending = [c for c in cues if not self._raw(c)]
                self.project.data['state'] = 'rendering'
                self.project.save()
                self._completed_cues = len(cues) - len(pending)
                self._total_cues = len(cues)
                self.emit('rendering', len(cues) - len(pending), len(cues))
                outer = self.concurrency
                inner = 1
                completed = len(cues) - len(pending)
                iterator = iter(pending)
                with ThreadPoolExecutor(max_workers=outer) as pool:
                    futures = {}
                    def submit():
                        cue = next(iterator, None)
                        if cue is not None:
                            audio.check_cancel(self.cancel_event)
                            cue['state'] = 'rendering'
                            futures[pool.submit(self._render, cue, config, inner)] = cue
                    for _ in range(min(outer, len(pending))):
                        submit()
                    try:
                        while futures:
                            done, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                            audio.check_cancel(self.cancel_event)
                            for future in done:
                                cue = futures.pop(future)
                                future.result()
                                completed += 1
                                self._completed_cues = completed
                                self.emit('rendering', completed, len(cues), cue)
                            for _ in done:
                                submit()
                    except Exception:
                        self.cancel()
                        for future in futures:
                            future.cancel()
                        raise
                self.project.save()
                self.project.data['state'] = 'fitting'
                self.emit('fitting', 0, len(cues))
                cues = sorted(cues, key=lambda cue: (cue['start_ms'], cue['source_order']))
                cursor = 0
                safety = audio.RATE * 30 // 1000
                borrow = audio.RATE // 4
                for i, cue in enumerate(cues):
                    audio.check_cancel(self.cancel_event)
                    raw = self.raw_path(cue)
                    metadata = cue['generation']['raw']
                    nominal_start = cue['start_ms'] * 48
                    nominal_end = cue['end_ms'] * 48
                    next_start = cues[i + 1]['start_ms'] * 48 if i + 1 < len(cues) else nominal_end
                    lead = min(borrow, max(0, nominal_start - cursor - safety))
                    tail = min(borrow, max(0, next_start - nominal_end - safety))
                    earliest = max(cursor, nominal_start - lead)
                    available_end = nominal_end + tail
                    window = max(1, available_end - earliest)
                    limit = 4. if policy == 'force' else max_tempo
                    key = audio.fit_identity(metadata['sha256'], window, policy, limit)
                    path = self.project.path(f'fit/{key}/cue.wav')
                    fit = cue.get('fit')
                    metadata_path = path.with_name('metadata.json')
                    if (not fit or fit.get('key') != key) and metadata_path.is_file():
                        try:
                            fit = read_json(metadata_path)
                            if not isinstance(fit, dict):
                                fit = None
                        except (ValueError, OSError):
                            fit = None
                    valid = (fit and fit.get('key') == key and fit.get('version') == audio.FIT_VERSION and
                             fit.get('target_frames') == window and path.is_file() and
                             audio.file_hash(path) == fit.get('sha256') and
                             audio.wave_frames(path) == fit.get('speech_frames'))
                    if not valid:
                        cue['state'] = 'fitting'
                        fit = audio.fit_cue(raw, path, window, limit, self.cancel_event)
                    actual = fit['speech_frames']
                    if actual <= available_end - earliest:
                        placed_start = max(cursor, min(nominal_start, available_end - actual))
                    else:
                        placed_start = earliest
                    placed_end = placed_start + actual
                    fit.update(file=path.relative_to(self.project.root).as_posix(), key=key,
                               nominal_start_frame=nominal_start, nominal_end_frame=nominal_end,
                               placed_start_frame=placed_start, placed_end_frame=placed_end,
                               shift_frames=placed_start - nominal_start,
                               overflow_frames=max(0, placed_end - available_end),
                               borrowed_lead_frames=lead, borrowed_tail_frames=tail)
                    atomic_json(metadata_path, fit)
                    cue['fit'] = fit
                    metadata['frames'] = fit['source_frames']
                    cue['state'] = 'ready'
                    cue.pop('error', None)
                    cursor = placed_end
                    self.checkpoint()
                    self.emit('fitting', i + 1, len(cues), cue)
                if render_only is not None:
                    self.project.data['state'] = 'pending'
                    self.project.save()
                    return self.project.path(cues[0]['fit']['file'])
                self.project.data['state'] = 'exporting'
                self.project.save()
                self.emit('exporting')
                export_id = identifier()
                report_path = self.project.path(f'exports/{export_id}/report.json')
                report = dict(version=1, export_id=export_id, identity=self.project.export_identity(),
                    project_id=self.project.data['id'], revision=self.project.data['revision'],
                    format=output_format, rate=audio.RATE, frames=audio.timeline_frames(cues),
                    cues=copy.deepcopy(cues), created_at=time.time(), state='exporting')
                atomic_json(report_path, report)
                placed = [dict(c, fit_path=self.project.path(c['fit']['file'])) for c in cues]
                result = audio.assemble(placed, destination, output_format, self.cancel_event)
                report.update(result=str(result), sha256=audio.file_hash(result), state='completed')
                atomic_json(report_path, report)
                self.project.data.update(state='completed', result=str(result),
                    export=dict(id=export_id, report=report_path.relative_to(self.project.root).as_posix()))
                self.project.save()
                self.emit('completed', len(cues), len(cues))
                return result
            except Exception:
                if self.project.data['state'] != 'needs_attention':
                    self.project.data['state'] = 'interrupted'
                try:
                    self.project.save()
                except OSError:
                    pass
                raise
