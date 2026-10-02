"""Fetch RUDRA's offline speech recogniser into the `speech` folder - pinned and verified.

    python windows\\fetch_speech.py                  # download what is missing, verify every file
    python windows\\fetch_speech.py --cache FOLDER   # use files already in FOLDER (verified) before downloading
    python windows\\fetch_speech.py --check          # download nothing; verify what is in speech\\

The recogniser is three open-source pieces, each pinned by an exact URL, size and SHA-256
(a file that does not match is refused and never used):

    whisper.cpp   the program that runs the model          MIT   github.com/ggml-org/whisper.cpp
    Whisper       OpenAI's speech model, ggml format       MIT   huggingface.co/ggerganov/whisper.cpp
    Silero VAD    the voice-activity model (ggml format)   MIT   huggingface.co/ggml-org/whisper-vad

The packaged application is built with these in `dist\\RUDRA\\speech` (`windows\\build.py`
runs this script). A source checkout needs them in `speech\\` for the microphone button and
`voice`; the folder is not committed (the model is 190 MB). Nothing is downloaded when
RUDRA runs: this is a build and set-up step only, and the installed program never connects
to the Internet for speech.

Written against the exact versions below on 2026-10-01; the licenses are as published at
those versions, and are installed with the files in `speech\\licenses`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEECH = ROOT / "speech"
LICENSES = Path(__file__).resolve().parent / "speech_licenses"

ENGINE = {
    "name": "whisper.cpp",
    "version": "v1.9.4 (release build b5130, commit 927cfce34f31707e17f2bff35c349632fb9e2c3a)",
    "url": "https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip",
    "size": 8573270,
    "sha256": "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c",
    "license": "MIT",
}
MODEL = {
    "name": "OpenAI Whisper small.en (ggml, 5-bit quantized)",
    "file": "ggml-small.en-q5_1.bin",
    "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/"
           "ggml-small.en-q5_1.bin",
    "size": 190098681,
    "sha256": "bfdff4894dcb76bbf647d56263ea2a96645423f1669176f4844a1bf8e478ad30",
    "license": "MIT",
}
VAD = {
    "name": "Silero VAD v5.1.2 (ggml)",
    "file": "ggml-silero-v5.1.2.bin",
    "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/"
           "ggml-silero-v5.1.2.bin",
    "size": 885098,
    "sha256": "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf",
    "license": "MIT",
}
#: What the program needs from the engine archive: the command line, the library and the
#: CPU back ends (the program picks the one that suits the processor at run time).
PROGRAM = "whisper-cli.exe"
PROGRAM_PREFIXES = ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu-")
LICENSE_FILES = ("WHISPER-CPP-LICENSE.txt", "OPENAI-WHISPER-LICENSE.txt", "SILERO-VAD-LICENSE.txt")
#: Words RUDRA shows with every transcript.
RECOGNIZER = "OpenAI Whisper small.en, run offline by whisper.cpp b5130"


def sha256(data: bytes | Path) -> str:
    digest = hashlib.sha256()
    if isinstance(data, Path):
        with data.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    else:
        digest.update(data)
    return digest.hexdigest()


def verified(path: Path, entry: dict) -> bool:
    return path.is_file() and path.stat().st_size == entry["size"] and sha256(path) == entry["sha256"]


def obtain(entry: dict, name: str, cache: Path | None) -> Path:
    """The pinned file, from `cache` when it holds a verified copy, otherwise downloaded."""
    if cache is not None:
        candidate = cache / name
        if verified(candidate, entry):
            print(f"  {name}: verified in {cache}")
            return candidate
    target = (cache or SPEECH / "_download") / name
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  {name}: downloading {entry['size'] / 1e6:.1f} MB from {entry['url']}")
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "RUDRA-fetch-speech"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:  # noqa: S310 - pinned https URL
        shutil.copyfileobj(response, out)
    if not verified(target, entry):
        target.unlink(missing_ok=True)
        raise SystemExit(f"{name}: the downloaded file does not match its pinned size and SHA-256; refused.")
    return target


def install(cache: Path | None) -> None:
    models = SPEECH / "models"
    models.mkdir(parents=True, exist_ok=True)
    print("whisper.cpp")
    archive = obtain(ENGINE, "whisper-bin-x64.zip", cache)
    with zipfile.ZipFile(archive) as bundle:
        wanted = [info for info in bundle.infolist()
                  if not info.is_dir() and info.filename.split("/")[-1].startswith(PROGRAM_PREFIXES)]
        if not any(Path(info.filename).name == PROGRAM for info in wanted):
            raise SystemExit(f"{PROGRAM} is not in the engine archive.")
        for info in wanted:
            (SPEECH / Path(info.filename).name).write_bytes(bundle.read(info))
    print("Whisper model")
    shutil.copyfile(obtain(MODEL, MODEL["file"], cache), models / MODEL["file"])
    print("Voice-activity model")
    shutil.copyfile(obtain(VAD, VAD["file"], cache), models / VAD["file"])
    licenses = SPEECH / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in LICENSE_FILES:
        shutil.copyfile(LICENSES / name, licenses / name)
    manifest = {
        "format": 1,
        "recognizer": RECOGNIZER,
        "threads": 4,
        "program": {"file": PROGRAM, "size": (SPEECH / PROGRAM).stat().st_size, "sha256": sha256(SPEECH / PROGRAM),
                    "name": ENGINE["name"], "version": ENGINE["version"], "license": ENGINE["license"],
                    "archive_sha256": ENGINE["sha256"]},
        "model": {"file": f"models/{MODEL['file']}", "size": MODEL["size"], "sha256": MODEL["sha256"],
                  "name": MODEL["name"], "license": MODEL["license"]},
        "vad": {"file": f"models/{VAD['file']}", "size": VAD["size"], "sha256": VAD["sha256"],
                "name": VAD["name"], "license": VAD["license"]},
    }
    (SPEECH / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(SPEECH / "_download", ignore_errors=True)


def check(folder: Path | None = None) -> list[str]:
    """Problems with what is installed in `folder`; empty when every file is the pinned one."""
    folder = folder or SPEECH
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for key, pinned in (("program", None), ("model", MODEL), ("vad", VAD)):
        entry = manifest[key]
        path = folder / entry["file"]
        if not verified(path, entry):
            problems.append(f"{path} is missing or does not match its recorded SHA-256")
        if pinned is not None and entry["sha256"] != pinned["sha256"]:
            problems.append(f"{path.name} is not the file this version of RUDRA pins")
    for name in LICENSE_FILES:
        if not (folder / "licenses" / name).is_file():
            problems.append(f"licenses\\{name} is missing")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache", type=Path, help="a folder that may already hold the pinned files")
    parser.add_argument("--check", action="store_true", help="download nothing; verify speech\\")
    args = parser.parse_args(argv)
    if not args.check:
        install(args.cache)
    problems = check()
    for problem in problems:
        print("PROBLEM:", problem)
    print("speech: OK" if not problems else "speech: NOT OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
