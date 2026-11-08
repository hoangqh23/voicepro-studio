"""Fetch the pinned upstream archive and stage only FFmpeg plus its notices.

The digest is pinned in vendor/ffmpeg/upstream.json. A moving 'latest' URL must
never silently change the build: a mismatch requires an explicit pin update.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
import tarfile
from pathlib import Path, PurePosixPath
try:
    from .utils import download_file
except ImportError:
    from utils import download_file


def prepare(archive_path=None, include_sources=False):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vendor = Path(__file__).resolve().parent / "vendor" / "ffmpeg"
    provenance = json.loads((vendor / "upstream.json").read_text(encoding="utf-8"))
    archive = Path(archive_path).resolve() if archive_path else vendor / provenance["asset_name"]
    if not archive.exists():
        download_file(provenance["download_url"], archive, provenance["archive_sha256"])
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != provenance["archive_sha256"]:
        raise ValueError("FFmpeg archive SHA256 mismatch; no binary was extracted or executed.")
    with zipfile.ZipFile(archive) as bundle:
        binary = [info for info in bundle.infolist() if info.filename.endswith("/bin/ffmpeg.exe")]
        if len(binary) != 1:
            raise ValueError("Expected exactly one static FFmpeg executable")
        prefix = PurePosixPath(binary[0].filename).parts[0]
        for info in bundle.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != prefix:
                raise ValueError("Unsafe path in FFmpeg archive")
            relative = Path(*path.parts[1:])
            is_notice = path.name.lower().startswith(("license", "copying", "readme"))
            if info.is_dir() or (info != binary[0] and not is_notice):
                continue
            target = vendor / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    executable = vendor / "bin" / "ffmpeg.exe"
    version = subprocess.run([str(executable), "-version"], capture_output=True, text=True, check=True).stdout
    if "--enable-gpl" in version or "--enable-nonfree" in version:
        raise ValueError("The pinned build is not the expected LGPL variant")
    license_result = subprocess.run([str(executable), "-L"], capture_output=True, text=True, check=True)
    # LGPLv3 supplements GPLv3, so retain both full texts in the distribution.
    gpl_text = vendor / "COPYING.GPLv3.txt"
    if not gpl_text.exists():
        download_file(f"https://raw.githubusercontent.com/FFmpeg/FFmpeg/{provenance['ffmpeg_source_commit']}/COPYING.GPLv3", gpl_text)
    with executable.open("rb") as stream:
        binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    provenance.update(binary_sha256=binary_digest,
                      version=version, license_output=license_result.stdout + license_result.stderr)
    sources = vendor / "sources"
    for field, name, prefix in (("ffmpeg_source_archive", "FFmpeg-source.tar.gz", "FFmpeg-" + provenance["ffmpeg_source_commit"]),
                                ("build_recipe_archive", "FFmpeg-build-recipe.tar.gz", "FFmpeg-Builds-" + provenance["build_recipe_commit"])):
        source = sources / name
        if include_sources and not source.exists():
            download_file(provenance[field], source)
        if source.exists():
            with tarfile.open(source, "r:gz") as bundle:
                member = bundle.next()
                if member is None or member.name.rstrip("/") != prefix:
                    raise ValueError("Source archive does not match the pinned commit")
            with source.open("rb") as stream:
                provenance[name + "_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    (vendor / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified FFmpeg: {executable}")
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sources", action="store_true")
    args = parser.parse_args()
    prepare(args.archive, args.sources)
