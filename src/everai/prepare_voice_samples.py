"""Download the site's existing demos once; never create TTS jobs for previews."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

try:
    from .voices import EVERAI_VOICES
    from .utils import atomic_json, download_file, ensure_ffmpeg
except ImportError:
    from voices import EVERAI_VOICES
    from utils import atomic_json, download_file, ensure_ffmpeg

SOURCE_PAGE = "https://www.everai.vn/text-to-speech"


class DemoLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identity = attrs.get("id", "")
        if not identity.startswith("playButton"):
            return
        match = re.search(r"onPlayClick\(\s*'([^']+)'", attrs.get("onclick", ""))
        if not match:
            return
        url = match.group(1)
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme == "https" and (host.endswith(".r2.cloudflarestorage.com") or host == "media.everaicdn.com") and parts.path.lower().endswith(".mp3"):
            self.links[identity[len("playButton"):]] = url


def verify_samples(directory):
    """Strict build check: every selectable voice must have its own verified asset."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    voices = manifest["voices"]
    if set(voices) != {voice["id"] for voice in EVERAI_VOICES}:
        raise ValueError("The demo manifest must contain exactly the selectable voices")
    for voice in EVERAI_VOICES:
        key = voice["id"]
        record = voices[key]
        if record["file"] != key + ".mp3":
            raise ValueError("Invalid voice sample filename")
        content = (directory / record["file"]).read_bytes()
        if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError("Voice sample digest mismatch: " + key)
        if not (content.startswith(b"ID3") or len(content) > 1 and content[0] == 255 and content[1] & 224 == 224):
            raise ValueError("Invalid MP3 demo: " + key)
    return manifest


def prepare(destination=None):
    destination = Path(destination or Path(__file__).resolve().parent / "assets" / "voice_samples")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        existing = verify_samples(destination)
        print(f"Verified {len(EVERAI_VOICES)} offline voice demos")
        return existing
    except (OSError, ValueError, KeyError, TypeError):
        pass
    response = requests.get(SOURCE_PAGE, timeout=30)
    response.raise_for_status()
    parser = DemoLinks()
    parser.feed(response.text)
    missing = [v["id"] for v in EVERAI_VOICES if v["id"] not in parser.links]
    if missing:
        raise ValueError("The public page has no demo for: " + ", ".join(missing))
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        raise ValueError("FFmpeg is required to validate voice demos")
    previous = {}
    try:
        previous = json.loads((destination / "manifest.json").read_text(encoding="utf-8")).get("voices", {})
    except (OSError, ValueError, TypeError):
        pass
    records = {}
    for index, voice in enumerate(EVERAI_VOICES, 1):
        key = voice["id"]
        path = destination / (key + ".mp3")
        source_url = parser.links[key]
        source_parts = urlsplit(source_url)
        # The page's migrated R2 links for static demos return 404; the site's
        # corresponding static files remain available at their original paths.
        if source_parts.path.startswith("/everai/static/audio/"):
            source_url = "https://www.everai.vn" + source_parts.path[len("/everai"):]
        old = previous.get(key, {})
        reusable = path.is_file() and path.stat().st_size == old.get("bytes") and hashlib.sha256(path.read_bytes()).hexdigest() == old.get("sha256")
        if not reusable:
            with tempfile.TemporaryDirectory(prefix="voice-demo-", dir=destination) as scratch:
                temporary = Path(scratch) / "demo.mp3"
                digest = download_file(source_url, temporary)
                if not 100 <= temporary.stat().st_size <= 16 * 1024 * 1024:
                    raise ValueError("Unexpected demo size: " + key)
                subprocess.run([str(ffmpeg), "-v", "error", "-xerror", "-i", str(temporary), "-f", "null", "-"],
                               capture_output=True, check=True, timeout=45,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                temporary.replace(path)
        else:
            digest = old["sha256"]
        source_digest = old.get("source_sha256", digest) if reusable else digest
        header = path.read_bytes()[:3]
        if not (header == b"ID3" or len(header) > 1 and header[0] == 255 and header[1] & 224 == 224):
            # Some public URLs end in .mp3 but contain WAV; normalize for MCI.
            with tempfile.TemporaryDirectory(prefix="voice-demo-mp3-", dir=destination) as scratch:
                converted = Path(scratch) / "normalized.mp3"
                subprocess.run([str(ffmpeg), "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "128k", str(converted)],
                               capture_output=True, check=True, timeout=45,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                converted.replace(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source = urlsplit(source_url)
        records[key] = dict(file=path.name, bytes=path.stat().st_size, sha256=digest, source_sha256=source_digest,
                            source=urlunsplit((source.scheme, source.netloc, source.path, "", "")))
        # Do not retain expiring signatures, cookies, or page HTML in the manifest.
        atomic_json(destination / "manifest.json", dict(version=1, source_page=SOURCE_PAGE, kind="original voice demos; not parameter-adjusted", voices={**previous, **records}))
        print(f"[{index}/{len(EVERAI_VOICES)}] {key}: ready")
    manifest = dict(version=1, source_page=SOURCE_PAGE, kind="original voice demos; not parameter-adjusted", voices=records)
    atomic_json(destination / "manifest.json", manifest)
    verify_samples(destination)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path)
    prepare(parser.parse_args().destination)
