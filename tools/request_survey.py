"""Opt-in live survey using the normal Voice Pro engine and license accounting.

python tools/request_survey.py --count 50
Only allowlisted metadata is recorded; credentials and signed URLs are excluded.
"""
import argparse
import csv
import hashlib
import itertools
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.everai import engine as engine_module
from src.everai.client import EverAIClient
from src.everai.proxy_manager import ProxyCatalog
from src.everai.license_manager import LicenseManager
from src.everai.utils import application_data_dir, read_json, parse_proxy
from src.everai.utils import redact_log

SAFE_HEADERS = {'user-agent', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
                'accept-language', 'accept', 'content-type', 'origin', 'referer',
                'sec-fetch-dest', 'sec-fetch-mode', 'sec-fetch-site', 'x-requested-with'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe_headers(headers):
    return {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() in SAFE_HEADERS}


def pairs(rows):
    result = []
    for left, right in itertools.combinations(rows, 2):
        a, b = left.get('create', {}), right.get('create', {})
        same_ip = bool(a.get('probe_ip') and a.get('probe_ip') == b.get('probe_ip'))
        same_fp = bool(a.get('fingerprint_sha256') and a.get('fingerprint_sha256') == b.get('fingerprint_sha256'))
        result.append(dict(left=left['run'], right=right['run'], comparable=bool(a and b),
                           same_probe_ip=same_ip, same_fingerprint=same_fp,
                           same_both=same_ip and same_fp))
    return result


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})


def report(output):
    """Rebuild reports from durable JSONL, including interrupted surveys."""
    from collections import defaultdict
    output = Path(output)
    runs = [json.loads(line) for line in (output / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]
    requests = [json.loads(line) for line in (output / 'requests.jsonl').read_text(encoding='utf-8').splitlines()]
    comparisons = pairs(runs)
    groups = defaultdict(list)
    for row in requests:
        groups[(row['channel'], row['method'], row['host'], row['path'], row['fingerprint_sha256'])].append(row)
    duplicates = []
    for (channel, method, host, path, fp), rows in groups.items():
        if len(rows) > 1:
            duplicates.append(dict(channel=channel, method=method, host=host, path=path,
                                   fingerprint_sha256=fp, sequences=[r['sequence'] for r in rows],
                                   runs=[r['run'] for r in rows], count=len(rows)))
    write_csv(output / 'http_fingerprint_groups.csv', duplicates)
    matched = [p for p in comparisons if p['same_fingerprint'] or p['same_probe_ip']]
    lines = ['# Khảo sát 50 lượt tạo audio — Voice Pro', '',
             f"Hoàn thành {len(runs)} lượt; {sum(r.get('success', False) for r in runs)} audio thành công.",
             f"Ghi {len(requests)} HTTP calls; so sánh {len(comparisons)} cặp create-request.", '',
             f"Cặp trùng fingerprint cấu hình: {sum(p['same_fingerprint'] for p in comparisons)}.",
             f"Cặp trùng IP probe: {sum(p['same_probe_ip'] for p in comparisons)}.",
             f"Cặp trùng cả hai: {sum(p['same_both'] for p in comparisons)}.", '',
             'IP probe đo qua ipify cùng tuyến trước TTS, không phải IP do máy chủ TTS xác nhận. '
             'Fingerprint là hash header cho phép ghi + cấu hình TLS; chưa đo JA3/JA4 thực tế. '
             'peer_ip có thể là địa chỉ proxy, không phải IP thoát.', '',
             '## Các cặp trùng', '', '| Lượt A | Lượt B | Trùng IP probe | Trùng fingerprint |',
             '|---|---|---|---|']
    lines += [f"| {p['left']} | {p['right']} | {p['same_probe_ip']} | {p['same_fingerprint']} |" for p in matched]
    lines += ['', '## Từng lượt', '', '| Lượt | Audio thành công | IP probe | Fingerprint SHA256 |', '|---|---|---|---|']
    for row in runs:
        create = row.get('create', {})
        lines.append(f"| {row['run']} | {row.get('success', False)} | {create.get('probe_ip', 'unknown')} | {create.get('fingerprint_sha256', 'unknown')} |")
    lines += ['', 'Chi tiết từng HTTP request: requests.csv / requests.jsonl. '
              'Toàn bộ cặp: pairs.csv. Nhóm HTTP call trùng fingerprint cùng endpoint: '
              'http_fingerprint_groups.csv (gồm cả polling/probe/download).', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--voice', default=None)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--website-default-model', action='store_true',
                        help='Omit model_id, matching the current official web client')
    args = parser.parse_args()
    if not 1 <= args.count <= 50:
        parser.error('count must be between 1 and 50')
    logging.disable(logging.CRITICAL)  # Never forward provider messages containing URLs/secrets.
    root = application_data_dir()
    settings = read_json(root / 'settings.json', {}) or {}
    output = args.output or Path('output/request-surveys') / datetime.now().strftime('%Y%m%d-%H%M%S')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    catalog = ProxyCatalog(root / 'proxies.json')
    catalog.stop_scheduler()  # Measurement must not trigger scheduled route changes.
    manager = LicenseManager(root)
    manager.ensure_active()
    voice = args.voice or settings.get('voice_id', 'vi_male_leduc_mb')
    if ':' in voice:
        raise ValueError('Select a native Voice Pro voice with --voice')
    config = dict(speaker_id=voice, model_id=settings.get('model_id', 'everai-v1.6'),
                  speed=1.0, pitch=1.0, volume=100, bitrate='128 kbps', audio_type='mp3')
    text = 'Xin chào, đây là bài kiểm tra giọng nói.'
    metadata = dict(count=args.count, text=text, config=config,
                    randomize_device=settings.get('randomize_device', True),
                    website_default_model=args.website_default_model,
                    ip_note='Probe to ipify through same session/route before TTS; not server-observed TTS IP.',
                    tls_note='Configured impersonation only; actual JA3/JA4 not measured.',
                    session_note='Fresh client per engine attempt; optional GUI RAM session is not available.')
    (output / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    requests, runs = [], []
    current = {}

    def append(name, row):
        with (output / name).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    class SurveyClient(EverAIClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.attempt = current.setdefault('attempts', 0) + 1
            current['attempts'] = self.attempt
            parsed = parse_proxy(self.proxy_str) if self.proxy_str else None
            route = f'{parsed.host}:{parsed.port}' if parsed else 'direct'
            for channel, session in [('tts', self.session), ('download', self.direct_session)]:
                original = session.request
                state = {'ip': None}

                def instrument(method, url, *a, _original=original, _session=session,
                               _channel=channel, _state=state, **kw):
                    parts = urlsplit(url)
                    if args.website_default_model and parts.path == '/text-to-speech/create-request':
                        kw['json'] = {k: v for k, v in kw['json'].items() if k != 'model_id'}
                    headers = safe_headers(dict(_session.headers))
                    headers.update(safe_headers(kw.get('headers') or {}))
                    fp = dict(headers=headers, impersonate=self.impersonate_target,
                              actual_ja3=None, actual_ja4=None)
                    row = dict(run=current['run'], attempt=self.attempt, channel=_channel,
                               sequence=len(requests) + 1, utc=datetime.now(timezone.utc).isoformat(),
                               method=method.upper(), host=parts.hostname,
                               path=parts.path if parts.hostname in ('www.everai.vn', 'api.ipify.org') else '[audio]',
                               route=route if _channel == 'tts' else 'direct',
                               probe_ip=_state['ip'], fingerprint=fp, fingerprint_sha256=digest(fp),
                               payload_sha256=digest(kw['json']) if 'json' in kw else None)
                    started = time.perf_counter()
                    try:
                        response = _original(method, url, *a, **kw)
                        row.update(status=response.status_code, response_bytes=len(response.content),
                                   http_version=str(getattr(response, 'http_version', 'unknown')),
                                   peer_ip=getattr(response, 'primary_ip', None))
                        if parts.path == '/text-to-speech/create-request':
                            try:
                                data = response.json()
                                row['application_status'] = data.get('status')
                                row['application_error_code'] = data.get('error_code')
                                row['application_error'] = redact_log(str(data.get('error_message', '')))[:300]
                            except (ValueError, AttributeError):
                                row['application_error'] = 'invalid_json'
                        if parts.hostname == 'api.ipify.org' and response.status_code == 200:
                            import ipaddress
                            _state['ip'] = str(ipaddress.ip_address(response.json()['ip']))
                            row['probe_ip'] = _state['ip']
                        return response
                    except Exception as exc:
                        row['error_type'] = type(exc).__name__
                        raise
                    finally:
                        row['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
                        requests.append(row)
                        append('requests.jsonl', row)
                        if parts.path == '/text-to-speech/create-request':
                            current['create'] = row

                session.request = instrument
                try:
                    session.get('https://api.ipify.org?format=json', timeout=20)
                except Exception:
                    pass

    try:
        with patch.object(engine_module, 'EverAIClient', SurveyClient):
            for number in range(1, args.count + 1):
                current = dict(run=number)
                started = time.perf_counter()
                engine = engine_module.EverAITTSEngine(
                    proxy_catalog=catalog, license_manager=manager, concurrency=1,
                    max_retries_per_chunk=1,
                    randomize_device=metadata['randomize_device'],
                    download_via_direct_ip=settings.get('download_via_direct_ip', True),
                    temp_dir=output / 'chunks')
                try:
                    path = engine.process(text, output / f'audio_{number:03}.mp3',
                                          job_dir=output / 'jobs' / f'{number:03}', **config)
                    current.update(success=True, audio=path.name, audio_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                except Exception as exc:
                    current.update(success=False, error_type=type(exc).__name__)
                current['elapsed_seconds'] = round(time.perf_counter() - started, 2)
                runs.append(dict(current))
                append('runs.jsonl', current)
                print(f"Run {number}/{args.count}: {'OK' if current['success'] else current['error_type']}", flush=True)
                time.sleep(1)
    finally:
        comparisons = pairs(runs)
        write_csv(output / 'requests.csv', requests)
        write_csv(output / 'runs.csv', runs)
        write_csv(output / 'pairs.csv', comparisons)
        summary = dict(completed_runs=len(runs), successful_audio=sum(r.get('success', False) for r in runs),
                       http_requests=len(requests), pairs=len(comparisons),
                       comparable_pairs=sum(p['comparable'] for p in comparisons),
                       same_probe_ip=sum(p['same_probe_ip'] for p in comparisons),
                       same_fingerprint=sum(p['same_fingerprint'] for p in comparisons),
                       same_both=sum(p['same_both'] for p in comparisons))
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        report(output)
        print(str(output), flush=True)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
