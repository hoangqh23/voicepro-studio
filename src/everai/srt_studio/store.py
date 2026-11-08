"""Atomic project storage, revisions and a process-wide project lease."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

from .model import (VERSION, MAX_BYTES, ID, canonical_config, digest, identifier,
                    parse_srt, speech_text, validate_cues)
try:
    from ..utils import atomic_json, read_json
except ImportError:
    from utils import atomic_json, read_json


@contextmanager
def project_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Project SRT đang mở tác vụ ở cửa sổ khác.') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class SrtProject:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.reload()

    @classmethod
    def create(cls, parent, source):
        source = Path(source)
        if source.stat().st_size > MAX_BYTES:
            raise ValueError('SRT vượt giới hạn 10 MiB.')
        raw = source.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError as error:
            raise ValueError('SRT cần mã hóa UTF-8 hoặc UTF-8 BOM.') from error
        cues, warnings = parse_srt(text)
        project_id = identifier()
        root = Path(parent).resolve() / project_id
        root.mkdir(parents=True, exist_ok=False)
        with project_lock(root):
            # Atomic byte snapshots use the same fsync/replace discipline as JSON.
            from .audio import atomic_bytes
            atomic_bytes(root / 'source.srt', raw)
            atomic_json(root / 'project.json', dict(version=VERSION, id=project_id, name=source.stem,
                revision=1, cues=cues, warnings=warnings, config=None, policy='bounded', max_tempo=1.35,
                format='wav', state='pending', result=None, created_at=time.time(), updated_at=time.time()))
        return cls(root)

    def reload(self):
        try:
            data = read_json(self.root / 'project.json')
            if not isinstance(data, dict) or data.get('version') != VERSION or not ID.fullmatch(str(data.get('id', ''))):
                raise ValueError('Phiên bản hoặc ID project SRT không hợp lệ.')
            validate_cues(data.get('cues'))
            if (type(data.get('revision')) is not int or data['revision'] < 1 or
                    not isinstance(data.get('name'), str) or not isinstance(data.get('state'), str) or
                    not isinstance(data.get('warnings'), list) or
                    any(not isinstance(warning, str) for warning in data['warnings'])):
                raise ValueError('Metadata project SRT không hợp lệ.')
            for cue in data['cues']:
                generation = cue.get('generation')
                if generation:
                    if (not isinstance(generation, dict) or not ID.fullmatch(str(generation.get('id', '')))
                            or not isinstance(generation.get('text'), str)):
                        raise ValueError('Generation cue không hợp lệ.')
                    canonical_config(generation.get('config'))
                    raw = generation.get('raw')
                    if raw is not None and (not isinstance(raw, dict) or
                            (raw.get('frames') is not None and (type(raw['frames']) is not int or raw['frames'] <= 0)) or
                            not isinstance(raw.get('sha256'), str) or
                            len(raw['sha256']) != 64):
                        raise ValueError('Metadata audio gốc SRT không hợp lệ.')
                    if generation.get('raw_file'):
                        root = self.generation_path(cue['id'], generation['id'])
                        if not self.path(generation['raw_file']).is_relative_to(root):
                            raise ValueError('Audio gốc không thuộc generation của cue.')
                fit = cue.get('fit')
                if fit is not None:
                    if not isinstance(fit, dict) or not isinstance(fit.get('file'), str):
                        raise ValueError('Metadata audio đã căn không hợp lệ.')
                    self.path(fit['file'])
            self.validate_options(data.get('policy'), data.get('max_tempo'), data.get('format'))
            if data.get('config') is not None:
                canonical_config(data['config'])
            self.data = data
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError) as error:
            raise ValueError('Không đọc được project SRT; file gốc được giữ nguyên.') from error
        return self

    @staticmethod
    def validate_options(policy, max_tempo, output_format):
        import math
        if (policy not in ('bounded', 'force') or output_format not in ('wav', 'mp3') or
                isinstance(max_tempo, bool) or not isinstance(max_tempo, (float, int)) or
                not math.isfinite(max_tempo) or not 1 <= max_tempo <= 4):
            raise ValueError('Chính sách căn SRT không hợp lệ (tăng tốc tối đa 1–4×).')

    def save(self):
        self.data['updated_at'] = time.time()
        atomic_json(self.root / 'project.json', self.data)

    def path(self, relative):
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError('Đường dẫn asset SRT không hợp lệ.')
        return candidate

    def generation_root(self, cue):
        return self.generation_path(cue['id'], cue['generation']['id'])

    def generation_path(self, cue_id, generation_id):
        return self.path(f'cues/{cue_id}/generations/{generation_id}')

    def cue(self, cue_id):
        result = next((c for c in self.data['cues'] if c['id'] == cue_id), None)
        if result is None:
            raise ValueError('Không tìm thấy cue trong project SRT.')
        return result

    def edit_cue(self, cue_id, text, start_ms, end_ms):
        self.edit_cues({cue_id: dict(text=text, start_ms=start_ms, end_ms=end_ms)})

    def edit_cues(self, changes):
        with project_lock(self.root):
            self.reload()
            previous = copy.deepcopy(self.data)
            self.data = copy.deepcopy(self.data)
            try:
                changed_ids = []
                for cue_id, values in changes.items():
                    cue = self.cue(cue_id)
                    if any(cue[k] != values[k] for k in ('text', 'start_ms', 'end_ms')):
                        cue.update({k: values[k] for k in ('text', 'start_ms', 'end_ms')})
                        cue['state'] = 'pending'
                        cue.pop('fit', None)
                        changed_ids.append(cue_id)
                validate_cues(self.data['cues'])
            except Exception:
                self.data = previous
                raise
            if not changed_ids:
                return
            self.data.update(revision=self.data['revision'] + 1, state='pending', result=None)
            try:
                self.save()
            except Exception:
                self.data = previous
                raise

    def remove_cue(self, cue_id):
        with project_lock(self.root):
            self.reload()
            cues = [c for c in self.data['cues'] if c['id'] != cue_id]
            validate_cues(cues)
            self.data.update(cues=cues, revision=self.data['revision'] + 1, state='pending', result=None)
            self.save()

    def regenerate(self, cue_id):
        with project_lock(self.root):
            self.reload()
            cue = self.cue(cue_id)
            cue.update(generation=None, state='pending')
            cue.pop('fit', None)
            self.data.update(revision=self.data['revision'] + 1, state='pending', result=None)
            self.save()

    def configure(self, config, policy='bounded', max_tempo=1.35, output_format='wav'):
        config = canonical_config(config)
        self.validate_options(policy, max_tempo, output_format)
        changed = any(self.data.get(k) != v for k, v in dict(config=config, policy=policy,
                      max_tempo=max_tempo, format=output_format).items())
        if changed:
            self.data.update(config=config, policy=policy, max_tempo=max_tempo, format=output_format,
                             revision=self.data['revision'] + 1, state='pending', result=None)
        for cue in self.data['cues']:
            generation = cue.get('generation')
            text = speech_text(cue['text'])
            if not generation or generation['text'] != text or generation['config'] != config:
                cue.update(generation=dict(id=identifier(), text=text, config=config), state='pending')
                cue.pop('fit', None)
        self.save()

    def export_identity(self):
        return digest(dict(revision=self.data['revision'], config=self.data['config'],
            policy=self.data['policy'], max_tempo=self.data['max_tempo'], format=self.data['format'],
            cues=[{k: c.get(k) for k in ('id', 'start_ms', 'end_ms', 'generation', 'fit')}
                  for c in self.data['cues']]))
