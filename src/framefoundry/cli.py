"""FrameFoundry command line.

  ./generate_video.sh [options]          == python -m framefoundry generate [options]
  ./framefoundry.sh <command> [options]  == python -m framefoundry <command> [options]
"""
import argparse
import json
import os
import sys
import traceback

from framefoundry import __version__
from framefoundry.util import ROOT, FrameFoundryError

EPILOG_GENERATE = """examples:
  ./generate_video.sh --script input/script.txt --voiceover input/narration.wav --logo input/logo.png
  ./generate_video.sh --script input/script.txt --language auto --website https://example.com --aspect-ratio 16:9
  ./generate_video.sh --script input/script.txt --language hinglish --aspect-ratio 9:16
  ./generate_video.sh --script input/script.txt --all-languages --aspect-ratio 16:9

script format: one narration line per row; '# Section' headings; *emphasis*; optional [web] [card] [flash]
[logo] [image:file] [video:file] tags; {tts: pronunciation}. See README.md and examples/.
Outputs go to output/<campaign>_NNN/ (final_video.mp4, qa_report.json, scene_timeline.md, ...)."""


def add_generate_args(ap):
    g = ap.add_argument_group("script and language")
    g.add_argument("--script", help="narration script (.txt or .md, UTF-8)")
    g.add_argument("--language", default="auto", choices=["auto", "english", "hindi", "hinglish"],
                   help="video language (default: auto-detect from the script)")
    g.add_argument("--all-languages", action="store_true",
                   help="create separate English, Hindi and Hinglish versions (uses script.<language>.txt variants when present)")
    for lang in ("english", "hindi", "hinglish"):
        g.add_argument(f"--script-{lang}", dest=f"script_{lang}", help=f"your own {lang} version of the script")
    g.add_argument("--convert-script", action="store_true",
                   help="allow rewriting Romanised Hindi <-> Devanagari for a single-language run (never done silently)")
    g = ap.add_argument_group("visual inputs")
    g.add_argument("--website", help="website URL to record with a real Chromium browser (read-only)")
    g.add_argument("--website-steps", help="steps file (.yaml/.json), instructions file (.txt) or literal instructions, "
                                          "e.g. \"open pricing; scroll down; hover Features\"")
    g.add_argument("--browser-profile", help="signed-in browser profile created with ./framefoundry.sh login")
    g.add_argument("--require-website", action="store_true", help="fail instead of continuing when recording fails")
    g.add_argument("--private-text", action="append", help="text to blur while recording (repeatable)")
    g.add_argument("--screen-recording", action="append", help="your own screen recording to use as footage (repeatable)")
    g.add_argument("--logo", help="brand logo (transparent PNG recommended; never distorted)")
    g.add_argument("--assets-dir", action="append", help="folder of images / videos / cut-out PNGs / fonts (repeatable)")
    g.add_argument("--asset", action="append", help="a single image or video file (repeatable)")
    g.add_argument("--brand", help="brand / product name (end card, logo reveal)")
    g.add_argument("--tagline", help="tagline for the end card")
    g.add_argument("--handle", help="text next to the logo watermark (default: website domain)")
    g = ap.add_argument_group("narration and audio")
    g.add_argument("--voiceover", help="your recorded narration (WAV/MP3/M4A/...) — skips voice generation")
    for lang in ("english", "hindi", "hinglish"):
        g.add_argument(f"--voiceover-{lang}", dest=f"voiceover_{lang}", help=f"narration for the {lang} version")
    g.add_argument("--voice-engine", choices=["auto", "chatterbox", "omnivoice", "none"],
                   help="local voice engine (default from config/voice_config.yaml)")
    g.add_argument("--voice-reference", help="authorised voice sample for cloning (needs --confirm-voice-permission)")
    g.add_argument("--confirm-voice-permission", action="store_true",
                   help="confirm you have the speaker's permission to reproduce their voice")
    g.add_argument("--voice-processing", choices=["light", "full"], default="light",
                   help="processing of an uploaded voice-over (light: clean-up + loudness; full: broadcast chain)")
    g.add_argument("--no-narration", action="store_true", help="music + text only video (timing from reading pace)")
    g.add_argument("--background-music", help="music track (looped/trimmed to fit, ducked under the voice)")
    g.add_argument("--music-from-reference", action="store_true",
                   help="extract the instrumental from the first --reference-video (needs --confirm-music-rights)")
    g.add_argument("--confirm-music-rights", action="store_true", help="confirm you may reuse that music")
    g = ap.add_argument_group("style")
    g.add_argument("--reference-video", action="append", help="reference video(s) to learn the editing style from")
    g.add_argument("--fast-reference", action="store_true", help="skip narration transcription in reference analysis")
    g.add_argument("--style-profile", help="profile name (see ./framefoundry.sh profiles) or a .yaml/.json path")
    g.add_argument("--save-profile", help="save the profile generated from --reference-video under this name")
    g.add_argument("--instructions", help="production instructions (text or file); key: value directives such as "
                                          "'subtitles: karaoke', 'transitions: fade', 'music_volume_db: -8' are applied")
    g.add_argument("--no-subtitles", action="store_true")
    g.add_argument("--karaoke", action="store_true", help="highlight each subtitle word as it is spoken")
    g = ap.add_argument_group("output")
    g.add_argument("--aspect-ratio", default="16:9", help="16:9 (default), 9:16, 1:1 or 4:5")
    g.add_argument("--resolution", help="exact size WIDTHxHEIGHT (overrides the aspect-ratio preset)")
    g.add_argument("--fps", type=int, help="frame rate (default 30)")
    g.add_argument("--duration", type=float, help="approximate target length in seconds")
    g.add_argument("--quality", choices=["draft", "standard", "high"], default="standard")
    g.add_argument("--crf", type=int, help="x264 CRF override")
    g.add_argument("--encoder", choices=["libx264", "h264_vaapi", "h264_nvenc"], help="video encoder (default libx264)")
    g.add_argument("--jobs", type=int, help="parallel render processes")
    g.add_argument("--output", help="campaign folder name or path (default: output/<script>_NNN)")
    g.add_argument("--overwrite", action="store_true", help="replace an existing --output campaign folder")
    g.add_argument("--plan-only", action="store_true", help="plan scenes and timing, skip mixing/rendering")
    g.add_argument("--keep-intermediates", action="store_true", default=True, help=argparse.SUPPRESS)


def finish_generate_opts(a):
    a.voiceover_by_lang = {k: getattr(a, f"voiceover_{k}") for k in ("english", "hindi", "hinglish") if getattr(a, f"voiceover_{k}")}
    a.script_by_lang = {k: getattr(a, f"script_{k}") for k in ("english", "hindi", "hinglish") if getattr(a, f"script_{k}")}
    a.output_name_hint = None
    return a


# ------------------------------------------------------------------------------------------ commands
def cmd_generate(a):
    from framefoundry.pipeline import generate
    return generate(finish_generate_opts(a))


def cmd_detect_language(a):
    from framefoundry.language import detect as D
    text = open(a.source, encoding="utf-8").read() if os.path.isfile(a.source) else a.source
    if os.path.isfile(a.source):
        from framefoundry.script.parser import parse
        _, lines = parse(text)
        text = "\n".join(L["plain"] for L in lines)
    res = D.detect(text)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"language: {res['language']} ({res['variant']}, {res['script']} script)  scores: {res['scores']}")
        for s in res["sentences"]:
            print(f"  [{s['language']:8s} {s['variant']:22s}] {s['text']}")
    return 0


def cmd_transliterate(a):
    from framefoundry.language import hinglish as H
    text = open(a.text, encoding="utf-8").read() if os.path.isfile(a.text) else a.text
    if a.to == "roman":
        print(H.transliterate_text(text, "roman"))
    else:
        print(H.hindi_words_to_devanagari(text))
    return 0


def cmd_profiles(a):
    from framefoundry.editing import styles
    if a.action == "show":
        prof = styles.load_profile(a.name)
        import yaml
        print(yaml.safe_dump({k: v for k, v in prof.items() if not k.startswith("_")}, allow_unicode=True, sort_keys=False))
        return 0
    rows = styles.list_profiles()
    if not rows:
        print("no profiles found")
    for r in rows:
        print(f"  {r['name']:24s} {'(local) ' if r['local'] else ''}{r['description']}")
    return 0


def cmd_analyze_reference(a):
    from framefoundry.config import get_config
    from framefoundry.editing import styles
    from framefoundry.reference import analyze as RA
    from framefoundry.reference import profile_builder as PB
    cfg = get_config()
    cfg.apply_environment()
    out = a.out or str(ROOT / "tmp" / "reference_analysis")
    analyses = []
    for k, v in enumerate(a.videos):
        if not os.path.isfile(v):
            raise FrameFoundryError(f"reference video not found: {v}")
        analyses.append(RA.analyze(v, os.path.join(out, f"ref_{k + 1:02d}"), cfg, transcribe=not a.fast))
    base = styles.load_profile(a.base)
    prof = PB.build(analyses, [os.path.basename(v) for v in a.videos], base)
    for n in prof.get("analysis", {}).get("notes", []):
        print(" -", n)
    if a.save_profile:
        p = styles.save_profile(prof, a.save_profile, overwrite=a.overwrite)
        print(f"saved style profile: {p}\nuse it with: ./generate_video.sh ... --style-profile {a.save_profile}")
    else:
        print(f"analysis written to {out} (add --save-profile NAME to save a reusable profile)")
    return 0


def cmd_login(a):
    from framefoundry.capture import login as L
    if a.forget:
        L.forget(a.forget)
        return 0
    if not a.url or not a.profile:
        raise FrameFoundryError("usage: ./framefoundry.sh login --url https://site --profile NAME   (or --forget NAME)")
    L.login(a.url, a.profile, a.timeout)
    return 0


def cmd_record(a):
    from framefoundry.capture import login as L
    from framefoundry.capture import website as WB
    from framefoundry.config import get_config
    cfg = get_config()
    cfg.apply_environment()
    out = a.out or str(ROOT / "output" / "recordings")
    prof = str(L.profile_dir(a.profile)) if a.profile else None
    meta = WB.record(a.url, out, cfg.main["browser"], WB.load_steps(a.steps, a.url), a.name, prof, a.private_text or [])
    print(json.dumps({k: meta.get(k) for k in ("status", "src", "duration", "error", "skipped")}, indent=1))
    return 0 if meta.get("status") == "ok" else 2


def cmd_voices(a):
    from framefoundry.config import get_config
    from framefoundry.voice import engines as E
    cfg = get_config()
    print(f"configured engine: {cfg.voice['engine']} · allow_non_commercial_models: {cfg.voice['allow_non_commercial_models']}")
    for name, e in E.ENGINES.items():
        ok, why = E.engine_status(name)
        print(f"  {name:11s} {'installed' if ok else why:60s}\n              {e['title']} — {e['licence']} — "
              f"commercial use: {'yes' if e['commercial'] else 'NO'}")
    print("  uploaded narration (--voiceover) always works and needs no model.")
    return 0


def cmd_models(a):
    from framefoundry.config import get_config
    cfg = get_config()
    cfg.apply_environment()
    if a.action == "list":
        d = cfg.path("models")
        total = 0
        for p in sorted(d.rglob("*")):
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        print(f"models folder: {d} ({total / 1e9:.2f} GB)")
        hub = d / "huggingface" / "hub"
        if hub.is_dir():
            for m in sorted(hub.glob("models--*")):
                print("  ", m.name.replace("models--", "").replace("--", "/"))
        return 0
    failed = 0
    if a.whisper or a.all:
        from faster_whisper.utils import download_model
        print("Whisper:", download_model(cfg.main["alignment"]["whisper_model"]))
    if a.chatterbox or a.all:
        from framefoundry.voice.engines import ENGINES
        py = ENGINES["chatterbox"]["python"]
        if py.exists():
            import subprocess
            r = subprocess.run([str(py), "-c", "from huggingface_hub import snapshot_download as s; print(s(repo_id='ResembleAI/chatterbox', "
                                "allow_patterns=['ve.pt','t3_mtl23ls_v3.safetensors','s3gen.pt','grapheme_mtl_merged_expanded_v1.json',"
                                "'conds.pt','Cangjie5_TC.json']))"], env=dict(os.environ))
            failed += r.returncode != 0
        else:
            print("Chatterbox is not installed (./install.sh --with-tts) — skipped")
    if a.translation or a.all:
        try:
            from transformers import MarianMTModel, MarianTokenizer
            for name in cfg.language["translation"]["models"].values():
                MarianTokenizer.from_pretrained(name)
                MarianMTModel.from_pretrained(name)
                print("translation:", name)
        except ImportError:
            print("translation needs the ML extras — skipped")
    if a.demucs or a.all:
        try:
            from demucs.pretrained import get_model
            get_model("htdemucs")
            print("Demucs: htdemucs")
        except ImportError:
            print("Demucs is not installed — skipped")
    return 1 if failed else 0


def cmd_rerender(a):
    """Re-render a campaign after editing project_files/edl.json (audio mix is reused)."""
    from framefoundry.config import get_config
    from framefoundry.qa.check import check
    from framefoundry.rendering.render import render_parallel
    cfg = get_config()
    cfg.apply_environment()
    d = os.path.abspath(a.campaign)
    edl_p = os.path.join(d, "project_files", "edl.json")
    audio = os.path.join(d, "final_audio.wav")
    if not os.path.isfile(edl_p) or not os.path.isfile(audio):
        raise FrameFoundryError(f"{d} is not a rendered FrameFoundry campaign folder (needs project_files/edl.json and final_audio.wav)")
    edl = json.load(open(edl_p, encoding="utf-8"))
    part = os.path.join(d, "final_video.partial.mp4")
    render_parallel(edl_p, audio, part, a.jobs, a.crf or 18, "medium")
    qa = check(part, edl, {"output_dir": d}, os.path.join(d, "qa_report.json"))
    final = os.path.join(d, "final_video.mp4" if qa["passed"] else "final_video.FAILED_QA.mp4")
    os.replace(part, final)
    print(("PASSED " if qa["passed"] else "FAILED ") + final)
    return 0 if qa["passed"] else 3


def cmd_doctor(a):
    from framefoundry.doctor import run_doctor
    return run_doctor(verbose=a.verbose)


# ------------------------------------------------------------------------------------------ parser
def build_parser():
    ap = argparse.ArgumentParser(prog="framefoundry", description=f"FrameFoundry {__version__} — From Script to Screen")
    ap.add_argument("--version", action="version", version=f"FrameFoundry {__version__}")
    ap.add_argument("--debug", action="store_true", help="show tracebacks")
    sub = ap.add_subparsers(dest="cmd", metavar="command")
    g = sub.add_parser("generate", help="produce a video (what ./generate_video.sh runs)", epilog=EPILOG_GENERATE,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    add_generate_args(g)
    g.set_defaults(func=cmd_generate)
    s = sub.add_parser("doctor", help="check the installation")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("detect-language", help="detect English / Hindi / Hinglish in a script or text")
    s.add_argument("source")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_detect_language)
    s = sub.add_parser("transliterate", help="Devanagari <-> casual Roman (Hinglish) spelling")
    s.add_argument("text", help="text or a file")
    s.add_argument("--to", choices=["roman", "devanagari"], required=True)
    s.set_defaults(func=cmd_transliterate)
    s = sub.add_parser("profiles", help="list or show style profiles")
    s.add_argument("action", nargs="?", default="list", choices=["list", "show"])
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_profiles)
    s = sub.add_parser("analyze-reference", help="learn a reusable style profile from reference video(s)")
    s.add_argument("videos", nargs="+")
    s.add_argument("--base", help="profile to start from (default: config default)")
    s.add_argument("--save-profile", help="name for the new profile (saved in templates/profiles/local/)")
    s.add_argument("--out", help="folder for the analysis files")
    s.add_argument("--fast", action="store_true", help="skip narration transcription")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=cmd_analyze_reference)
    s = sub.add_parser("login", help="sign in to a website manually in a visible browser (session kept locally)")
    s.add_argument("--url")
    s.add_argument("--profile", help="name for the saved browser profile")
    s.add_argument("--forget", help="delete a saved browser profile")
    s.add_argument("--timeout", type=float, default=20, help="minutes to wait (default 20)")
    s.set_defaults(func=cmd_login)
    s = sub.add_parser("record", help="record a website without making a video")
    s.add_argument("--url", required=True)
    s.add_argument("--steps", help="steps file or instructions")
    s.add_argument("--profile", help="signed-in browser profile name")
    s.add_argument("--name", default="rec_01")
    s.add_argument("--out", help="output folder (default output/recordings)")
    s.add_argument("--private-text", action="append")
    s.set_defaults(func=cmd_record)
    s = sub.add_parser("voices", help="list voice engines, install status and licences")
    s.set_defaults(func=cmd_voices)
    s = sub.add_parser("models", help="list or download model weights (into models/)")
    s.add_argument("action", nargs="?", default="list", choices=["list", "download"])
    s.add_argument("--all", action="store_true")
    s.add_argument("--whisper", action="store_true")
    s.add_argument("--chatterbox", action="store_true")
    s.add_argument("--translation", action="store_true")
    s.add_argument("--demucs", action="store_true")
    s.set_defaults(func=cmd_models)
    s = sub.add_parser("rerender", help="re-render a campaign after editing its project_files/edl.json")
    s.add_argument("campaign")
    s.add_argument("--jobs", type=int)
    s.add_argument("--crf", type=int)
    s.set_defaults(func=cmd_rerender)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.cmd == "models" and a.action == "download" and not any((a.all, a.whisper, a.chatterbox, a.translation, a.demucs)):
        a.all = True
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    try:
        return a.func(a) or 0
    except FrameFoundryError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:            # e.g. `... | head -1` — not an error
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0
    except Exception as e:  # noqa: BLE001
        if a.debug or os.environ.get("FRAMEFOUNDRY_DEBUG"):
            traceback.print_exc()
        else:
            print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}\n(re-run with --debug for the traceback)", file=sys.stderr)
        return 1
