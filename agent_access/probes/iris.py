"""EA's IRIS (BSD-3) for 2.3.1 and 2.3.2, used only when installed (plan 3.4): its example app `IrisApp` (with the
appsettings.json its build puts beside it) and ffmpeg on PATH. A few seconds of the page are recorded through
Chromium's screencast and turned into a constant-rate video, and IRIS counts luminance and red flashes over the WCAG
general flash area. Its pattern detection is off: spatial patterns aren't a WCAG criterion.

IRIS judges the frame's average luminance, made for full-screen video: a flash under about a tenth of a 1280 x 800
viewport never moves that average enough, however bright. So it only adds fails (flicker too fast for the flashing
probe's screenshot sampler); the sampler's grid still decides everything else.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

IRIS_SECONDS = 3
IRIS_FPS = 30
FAILURES = ("LuminanceFlashFailure", "RedFlashFailure")   # more than 3 flashes in a second (IRIS's 2-3 Hz extended
                                                          # failure needs 5 s and isn't a WCAG failure)


def iris_command() -> list[str] | None:
    app = shutil.which("IrisApp")
    return [app] if app and shutil.which("ffmpeg") else None


async def _record(page, seconds: float) -> list[tuple[float, bytes]]:
    """Screencast frames (timestamp, JPEG) for `seconds`: Chromium sends one whenever the page repaints."""
    cdp = await page.context.new_cdp_session(page)
    frames: list[tuple[float, bytes]] = []
    acks = []

    def on_frame(event):
        frames.append((event["metadata"]["timestamp"], base64.b64decode(event["data"])))
        acks.append(asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})))
    cdp.on("Page.screencastFrame", on_frame)
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "everyNthFrame": 1})
    await asyncio.sleep(seconds)
    await cdp.send("Page.stopScreencast")
    await asyncio.gather(*acks, return_exceptions=True)   # before the session goes, or they fail unread
    await cdp.detach()
    return frames


def _video(frames: list[tuple[float, bytes]], tmp: Path) -> Path:
    """The frames as an IRIS_FPS video: each tick shows the last frame painted by then (nothing changed in between)."""
    i = 0
    for k in range(IRIS_SECONDS * IRIS_FPS):
        while i + 1 < len(frames) and frames[i + 1][0] <= frames[0][0] + k / IRIS_FPS:
            i += 1
        (tmp / f"f{k:05d}.jpg").write_bytes(frames[i][1])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(IRIS_FPS), "-i", str(tmp / "f%05d.jpg"),
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p",
                    str(tmp / "page.mp4")], check=True, capture_output=True, timeout=120)
    return tmp / "page.mp4"


def _analyse(command: list[str], video: Path, area: float, tmp: Path) -> dict:
    """IRIS's verdict on `video`, counting a flash only where it covers `area` of the frame."""
    run = tmp / f"area-{area:.6f}"
    run.mkdir()
    shutil.copy(Path(command[-1]).parent / "appsettings.json", run)   # IrisApp reads it from its working directory
    subprocess.run([*command, "-v", str(video), "-j", "1", "-p", "0", "-a", f"{area:.6f}"], cwd=run, check=True,
                   capture_output=True, timeout=300)
    r = json.loads((run / "Results" / video.name / "result.json").read_text())
    return {"results": r.get("Results", []), "luminance_fail_frames": r["TotalLuminanceIncidents"]["FlashFailFrames"],
            "red_fail_frames": r["TotalRedIncidents"]["FlashFailFrames"]}


async def iris_flashes(page, viewport: dict, flash_area_px: int) -> dict | None:
    """IRIS's results for the page, or None when it isn't installed (or {"error"} when it couldn't run)."""
    if (command := iris_command()) is None:
        return None
    try:
        frames = await _record(page, IRIS_SECONDS)
    except Exception as exc:   # the page went away mid-recording: the sampler's measurement still stands
        return {"error": f"the screencast failed: {type(exc).__name__}"}
    if not frames:
        return {"error": "the screencast sent no frames"}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            video = await asyncio.to_thread(_video, frames, Path(tmp))
            result = await asyncio.to_thread(_analyse, command, video,
                                             flash_area_px / (viewport["width"] * viewport["height"]), Path(tmp))
        except Exception as exc:   # a missing codec, a crash, output that isn't its JSON: the sampler still decides
            return {"error": f"IRIS couldn't run: {type(exc).__name__}"}
    return {"seconds": IRIS_SECONDS, "fps": IRIS_FPS, "painted_frames": len(frames), **result}
