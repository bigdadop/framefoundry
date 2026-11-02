"""Render an EDL to H.264: frames from the compositor are piped to FFmpeg.

Parallel rendering splits the timeline into frame-aligned segments rendered by separate processes,
concatenates them losslessly (stream copy) and muxes the mixed audio (AAC).

  python -m framefoundry.rendering.render EDL.json OUT.mp4 [--from T0] [--to T1]   (one segment, no audio)
  python -m framefoundry.rendering.render EDL.json OUT_DIR --frames-at 1.0,2.5      (PNG stills)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from framefoundry.util import FrameFoundryError, cpu_count, ffmpeg, ffmpeg_bin, info

ENCODERS = ("libx264", "h264_vaapi", "h264_nvenc")


def encoder_args(encoder, crf, preset):
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf + 1), "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if encoder == "h264_vaapi":
        return ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", str(crf + 2), "-profile:v", "high"]
    raise FrameFoundryError(f"unknown encoder {encoder} (choose {', '.join(ENCODERS)})")


def _pre_input_args(encoder):
    if encoder == "h264_vaapi":
        dev = os.environ.get("FF_VAAPI_DEVICE", "/dev/dri/renderD128")
        return ["-vaapi_device", dev]
    return []


def render_segment(edl, out, t0=0.0, t1=None, crf=18, preset="medium", encoder="libx264", motion_blur=True, report=None):
    from framefoundry.rendering import compositor as C
    C.configure(edl)
    fps = C.FPS
    t1 = edl["duration"] if t1 is None else t1
    f0, f1 = int(round(t0 * fps)), int(round(t1 * fps))
    n = max(0, f1 - f0)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *_pre_input_args(encoder),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{C.W}x{C.H}", "-r", str(fps), "-i", "-",
           *encoder_args(encoder, crf, preset), "-r", str(fps), "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(n):
            t = (f0 + i) / fps
            p.stdin.write(C.render_frame(edl, t, motion_blur).convert("RGB").tobytes())
            if report and (i % 15 == 0 or i == n - 1):
                report(i + 1, n)
        p.stdin.close()
    except BrokenPipeError:
        pass
    err = p.stderr.read().decode(errors="replace")
    rc = p.wait()
    if rc:
        raise FrameFoundryError(f"FFmpeg encoder failed ({encoder}): {err.strip()[-800:]}")
    return out


def encoder_works(encoder):
    """Probe a hardware encoder with a tiny encode (drivers / libva versions vary between machines)."""
    if encoder == "libx264":
        return True, ""
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *_pre_input_args(encoder), "-f", "lavfi",
               "-i", "testsrc2=size=320x240:rate=30", "-t", "0.2", *encoder_args(encoder, 23, "fast"), os.path.join(d, "p.mp4")]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return False, "probe timed out"
        return r.returncode == 0, (r.stderr or "").strip()[-300:]


def default_jobs():
    return max(1, min(8, cpu_count() - 1))


def render_parallel(edl_path, audio, out, jobs=None, crf=18, preset="medium", encoder="libx264", motion_blur=True,
                    audio_codec="aac", audio_bitrate="256k", sample_rate=48000):
    edl = json.load(open(edl_path, encoding="utf-8"))
    fps = int(edl.get("fps", 30))
    nframes = int(round(edl["duration"] * fps))
    ok, why = encoder_works(encoder)
    if not ok:
        from framefoundry.util import warn
        warn(f"hardware encoder {encoder} does not work on this system ({why.splitlines()[-1] if why else 'unknown error'}); "
             "using libx264 on the CPU instead")
        encoder = "libx264"
    jobs = max(1, min(jobs or default_jobs(), max(1, nframes // 45)))
    tmp = tempfile.mkdtemp(prefix=".render_", dir=os.path.dirname(os.path.abspath(out)))
    bounds = [round(nframes * k / jobs) for k in range(jobs + 1)]
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # .../src
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENCV_FOR_THREADS_NUM="1", PYTHONUNBUFFERED="1",
               PYTHONPATH=src_dir + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
    procs, done = [], [0] * jobs
    info(f"rendering {nframes} frames ({edl['w']}x{edl['h']} @ {fps} fps) with {jobs} parallel worker(s), encoder {encoder}")
    t_start = time.time()
    try:
        for k in range(jobs):
            seg = os.path.join(tmp, f"seg_{k:02d}.mp4")
            cmd = [sys.executable, "-m", "framefoundry.rendering.render", edl_path, seg,
                   "--from", f"{bounds[k] / fps:.6f}", "--to", f"{bounds[k + 1] / fps:.6f}",
                   "--crf", str(crf), "--preset", preset, "--encoder", encoder]
            if not motion_blur:
                cmd.append("--no-motion-blur")
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            procs.append((p, seg))

        def pump(k, p):
            for line in p.stdout:
                if line.startswith("FRAMES "):
                    done[k] = int(line.split()[1])
        threads = [threading.Thread(target=pump, args=(k, p), daemon=True) for k, (p, _) in enumerate(procs)]
        for th in threads:
            th.start()
        last = 0.0
        while any(p.poll() is None for p, _ in procs):
            time.sleep(0.5)
            if time.time() - last > 8:
                last = time.time()
                frac = sum(done) / max(1, nframes)
                eta = (time.time() - t_start) / max(frac, 1e-3) * (1 - frac)
                info(f"  frames {sum(done)}/{nframes} ({frac * 100:.0f}%), ~{eta:.0f}s left")
        for th in threads:
            th.join(timeout=2)
        for p, seg in procs:
            err = p.stderr.read()
            if p.returncode:
                raise FrameFoundryError(f"render worker failed for {os.path.basename(seg)}:\n{err[-2500:]}")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.write("".join(f"file '{seg}'\n" for _, seg in procs))
        video = os.path.join(tmp, "video.mp4")
        ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)
        if audio:
            acodec = ["-c:a", "copy"] if str(audio).endswith((".m4a", ".aac")) else \
                ["-c:a", audio_codec, "-b:a", audio_bitrate, "-ar", str(sample_rate)]
            ffmpeg("-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *acodec,
                   "-t", f"{edl['duration']:.3f}", "-movflags", "+faststart", out)
        else:
            os.replace(video, out)
        info(f"render finished in {time.time() - t_start:.0f}s")
    finally:
        for p, _ in procs:
            if p.poll() is None:
                p.kill()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def render_stills(edl, out_dir, times):
    from framefoundry.rendering import compositor as C
    C.configure(edl)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for t in times:
        p = os.path.join(out_dir, f"frame_{t:07.2f}.jpg")
        C.render_frame(edl, t).convert("RGB").save(p, quality=90)
        paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser(description="Render an EDL segment (used by the parallel renderer).")
    ap.add_argument("edl")
    ap.add_argument("out")
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--frames-at", default=None)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--encoder", default="libx264", choices=ENCODERS)
    ap.add_argument("--no-motion-blur", action="store_true")
    a = ap.parse_args()
    edl = json.load(open(a.edl, encoding="utf-8"))
    if a.frames_at:
        for p in render_stills(edl, a.out, [float(x) for x in a.frames_at.split(",")]):
            print(p)
        return 0

    def report(i, n):
        print(f"FRAMES {i} {n}", flush=True)
    render_segment(edl, a.out, a.t0, a.t1, a.crf, a.preset, a.encoder, not a.no_motion_blur, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
