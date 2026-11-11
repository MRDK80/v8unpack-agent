"""Produce and record actual text inputs for the fixed synthetic image cases."""
from __future__ import annotations

import argparse
import base64
import io
import os
import subprocess
import tempfile
from pathlib import Path

from scripts.image_cases_issue520 import bounded_image, sha, write
from scripts.unicode_study_issue519 import read

PIXEL_CHANNELS = {"visible_pixels", "low_contrast", "alpha"}
OCR_ARGS = ["input.png", "stdout", "-l", "eng", "--oem", "1", "--psm", "6", "--dpi", "300"]


def tool_config(prefix):
    executable = prefix / "usr/bin/tesseract"
    language = prefix / "usr/share/tesseract-ocr/5/tessdata"
    libraries = prefix / "usr/lib/x86_64-linux-gnu"
    env = dict(os.environ, PATH=str(executable.parent) + os.pathsep + os.defpath,
               TESSDATA_PREFIX=str(language), LD_LIBRARY_PATH=str(libraries),
               OMP_THREAD_LIMIT="1", LC_ALL="C.UTF-8")
    version = subprocess.check_output(["tesseract", "--version"], env=env, timeout=10)
    if not version.startswith(b"tesseract 5.3.4\n"):
        raise ValueError("unexpected Tesseract version")
    identity = {
        "version_stdout": version.decode("utf-8"),
        "binary_sha256": sha(executable.read_bytes()),
        "eng_sha256": sha((language / "eng.traineddata").read_bytes()),
        "libraries": {p.name: sha(p.read_bytes()) for p in sorted(libraries.glob("*.so.*")) if p.is_file()},
        "argv": ["tesseract", *OCR_ARGS],
        "timeout_seconds": 20,
        "threads": 1,
        "locale": "C.UTF-8",
    }
    return env, identity


def ocr(raw, target, env):
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        rgba = image.convert("RGBA")
        raster = Image.alpha_composite(Image.new("RGBA", image.size, "white"), rgba).convert("RGB")
        raster.save(target, format="PNG", dpi=(300, 300), compress_level=9)
    raster_bytes = target.read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / "input.png").write_bytes(raster_bytes)
        try:
            result = subprocess.run(["tesseract", *OCR_ARGS], cwd=folder, env=env,
                                    capture_output=True, timeout=20, check=False)
            stdout, stderr = result.stdout, result.stderr
            code = result.returncode
            status = "completed" if code == 0 else "tool_error"
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            code, status = None, "timeout"
    try:
        observed_text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        observed_text, status = None, "invalid_utf8"
    return {
        "status": status,
        "exit_code": code,
        "stdout_base64": base64.b64encode(stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(stderr).decode("ascii"),
        "text": observed_text if status == "completed" else None,
        "raster_sha256": sha(raster_bytes),
        "raster_file": target.name,
    }


def prepare(data, prefix):
    if (data / "observations.json").exists() or (data / "ocr").exists():
        raise ValueError("OCR artifacts already exist. Preserve the original processing attempt.")
    env, identity = tool_config(prefix)
    directory = data / "ocr"
    directory.mkdir()
    observations = {}
    for case in read(data / "cases.json"):
        if case["route_status"] != "active":
            continue
        path = data / case["file"]
        raw, info = bounded_image(path)
        if info["sha256"] != case["image"]["sha256"]:
            raise ValueError("original image drift")
        channel = case["channel"]
        if channel == "filename":
            observation = {"route": "filename_text", "text": path.name, "status": "completed"}
        elif channel == "metadata":
            observation = {"route": "metadata_text", "text": info["metadata_text"], "status": "completed"}
        else:
            assert channel in PIXEL_CHANNELS
            observation = ocr(raw, directory / (case["id"] + ".png"), env)
            observation["route"] = "ocr_text"
        observations[case["id"]] = observation
        write(directory / (case["id"] + ".json"), observation)
    write(data / "ocr-tool.json", identity)
    write(data / "observations.json", observations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("prefix", type=Path)
    args = parser.parse_args()
    prepare(args.data.resolve(), args.prefix.resolve())
