"""The production pipeline: inputs -> campaign folder(s) with a QA-verified final_video.mp4.

Shared work (assets, website recording, reference analysis, music) is done once; narration, edit,
mix, render and QA are done per language. A video only becomes final_video.mp4 after QA passes;
otherwise it is kept as final_video.FAILED_QA.mp4 and the command exits non-zero.
"""
import os
import platform
import re
import shutil
import time
from importlib import metadata

from framefoundry import __version__
from framefoundry.config import ASPECTS, get_config
from framefoundry.editing import editor, styles, timeline
from framefoundry.language import detect as D
from framefoundry.language import translate as T
from framefoundry.project import campaign_dir, subdirs
from framefoundry.script import parser as P
from framefoundry.script import planner
from framefoundry.util import (ROOT, FrameFoundryError, file_sig, info, jdump, media_info, set_log_file, step, util_versions,
                               warn)

QUALITY = {"draft": {"crf": 23, "preset": "veryfast"}, "standard": {"crf": 18, "preset": "medium"},
           "high": {"crf": 16, "preset": "slow"}}


def _versions():
    out = {"framefoundry": __version__, "python": platform.python_version()}
    for pkg in ("numpy", "opencv-python-headless", "pillow", "playwright", "faster-whisper", "ctranslate2", "torch", "demucs",
                "transformers", "librosa"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    out.update(util_versions())
    return out


# ------------------------------------------------------------------------------------------ inputs
def _need_file(path, what):
    if path and not os.path.isfile(path):
        raise FrameFoundryError(f"{what} not found: {path}")


def validate(o, cfg):
    if not o.script and not o.voiceover:
        raise FrameFoundryError("give a --script (TXT or Markdown), or at least a --voiceover to transcribe")
    for path, what in ((o.script, "--script"), (o.voiceover, "--voiceover"), (o.logo, "--logo"),
                       (o.background_music, "--background-music"), (o.voice_reference, "--voice-reference")):
        _need_file(path, what)
    for lang, p in (o.voiceover_by_lang or {}).items():
        _need_file(p, f"--voiceover-{lang}")
    for lang, p in (o.script_by_lang or {}).items():
        _need_file(p, f"--script-{lang}")
    for p in o.reference_video or []:
        _need_file(p, "--reference-video")
    for p in o.screen_recording or []:
        _need_file(p, "--screen-recording")
    if o.language not in D.ALLOWED:
        raise FrameFoundryError(f"--language must be one of {', '.join(D.ALLOWED)}")
    if o.voice_reference and not o.confirm_voice_permission:
        raise FrameFoundryError("--voice-reference clones a real person's voice. Only do this with the speaker's permission "
                                "and add --confirm-voice-permission to confirm it.")
    if o.music_from_reference and not o.confirm_music_rights:
        raise FrameFoundryError("--music-from-reference reuses another video's music. Add --confirm-music-rights to confirm "
                                "you own or are licensed to reuse it.")
    if o.music_from_reference and not o.reference_video:
        raise FrameFoundryError("--music-from-reference needs a --reference-video")
    if o.aspect_ratio not in ASPECTS and not o.resolution:
        raise FrameFoundryError(f"--aspect-ratio must be one of {', '.join(ASPECTS)} (or use --resolution WxH)")
    if o.fps and not 12 <= o.fps <= 60:
        raise FrameFoundryError("--fps must be between 12 and 60")
    if o.website and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", o.website):
        raise FrameFoundryError(f"--website must be a full http(s) URL, got '{o.website}'")


def parse_instructions(text):
    """Optional production instructions. Recognised 'key: value' directives are applied; everything else is
    kept in the manifest for the person / AI assistant planning the video (FrameFoundry has no language model)."""
    if text and os.path.isfile(text):
        text = open(text, encoding="utf-8").read()
    applied, rest = {}, []
    keys = {"subtitles", "style", "music_volume_db", "transitions", "end_card", "karaoke", "stickers", "pace"}
    for line in re.split(r"\n|;|\\n", text or ""):        # newlines, ';' or a literal "\n" separate directives
        m = re.match(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$", line, re.I)
        if m and m.group(1).lower() in keys:
            applied[m.group(1).lower()] = m.group(2)
        elif line.strip():
            rest.append(line.strip())
    return applied, rest


def apply_directives(profile, d, notes):
    on = {"on", "yes", "true", "1"}
    if "subtitles" in d:
        v = d["subtitles"].lower()
        profile["subtitles"]["enabled"] = v != "off" and v not in ("no", "false", "0")
        if v == "karaoke":
            profile["subtitles"]["karaoke"] = True
        if v in ("all", "everywhere"):
            profile["subtitles"]["on_cards"] = True
    if "karaoke" in d:
        profile["subtitles"]["karaoke"] = d["karaoke"].lower() in on
    if "music_volume_db" in d:
        profile["music"]["bed_db"] = float(d["music_volume_db"])
    if "transitions" in d and d["transitions"] in ("cut", "whip", "punch", "fade"):
        for k in ("web_web", "card_web", "web_card"):
            profile["transitions"][k] = d["transitions"]
    if "end_card" in d:
        profile["structure"]["end_card"] = d["end_card"].lower() in on
    if "stickers" in d:
        profile["cards"]["stickers"] = d["stickers"].lower() in on
    notes.append(f"production directives applied: {d}")


# ------------------------------------------------------------------------------------------ helpers
def _timing_no_narration(lines, wps, target=None, lead=0.4):
    durs = [max(1.3, len(L["plain"].split()) / max(1.0, wps) + 0.35) for L in lines]
    if target:
        k = max(0.5, (target - 4.0 - lead) / max(1e-3, sum(durs)))
        durs = [d * k for d in durs]
    t, timing = lead, {}
    from framefoundry.audio.align import estimate, spoken_words
    for L, d in zip(lines, durs):
        ws = estimate(spoken_words(L["plain"]), t + 0.1, t + d - 0.25)
        timing[L["id"]] = {"start": round(t, 3), "end": round(t + d - 0.15, 3),
                           "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in ws], "words_method": "estimated (no narration)"}
        t += d
    return timing


def _script_from_voiceover(path, cfg, work):
    from framefoundry.audio.align import transcribe
    step("No script given: transcribing the voice-over to build one")
    tmp = os.path.join(work, "vo_for_transcript.wav")
    from framefoundry.audio.process import decode
    decode(path, tmp, 16000, 1)
    heard, inf = transcribe(tmp, None, cfg, vad=True)
    text = inf["text"].strip()
    if not text:
        raise FrameFoundryError("the voice-over contains no recognisable speech")
    sents = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
    info(f"transcribed {len(heard)} words ({inf['language']}); review project_files/transcribed_script.txt")
    return "\n".join(sents) + "\n"


def _brand(o, meta):
    website = o.website or meta.get("website") or ""
    domain = re.sub(r"^https?://(www\.)?", "", website).strip("/").split("/")[0] if website else ""
    name = o.brand or meta.get("brand") or ""
    return {"name": name, "tagline": o.tagline if o.tagline is not None else (meta.get("tagline") or ""),
            "handle": o.handle or meta.get("handle") or domain, "bg_word": meta.get("bg_word") or name,
            "website": website}


# ------------------------------------------------------------------------------------------ main entry
def generate(o):
    cfg = get_config()
    cfg.apply_environment()
    validate(o, cfg)
    t_all = time.time()
    hint = o.output_name_hint or (os.path.splitext(os.path.basename(o.script))[0] if o.script else "campaign")
    root = campaign_dir(cfg.output_dir(), o.output, hint, o.overwrite)
    set_log_file(root / "generation.log")
    step(f"FrameFoundry {__version__} — campaign folder {root}")
    notes_global = []
    work = root / "project_files"
    work.mkdir(parents=True, exist_ok=True)

    # ---- script + language ----------------------------------------------------------------
    if o.script:
        meta, src_lines, raw = P.load_script(o.script)
    else:
        raw = _script_from_voiceover(o.voiceover, cfg, str(work))
        (work / "transcribed_script.txt").write_text(raw, encoding="utf-8")
        meta, src_lines = P.parse(raw)
        notes_global.append("script transcribed from the voice-over (no --script given)")
    if meta.get("_unknown_keys"):
        warn(f"ignored unknown front-matter keys: {meta['_unknown_keys']}")
    src_lines = P.split_long(src_lines)
    det = D.detect("\n".join(L["plain"] for L in src_lines))
    source_lang = det["language"]
    requested = (o.language or "auto").lower()
    if o.all_languages:
        targets = list(cfg.language["all_languages"])
    else:
        targets = [source_lang if requested == "auto" else requested]
    info(f"script: {len(src_lines)} lines, detected {source_lang} ({det['variant']}, {det['script']} script); "
         f"producing: {', '.join(targets)}")

    # ---- style profile --------------------------------------------------------------------
    profile = styles.load_profile(o.style_profile or meta.get("style_profile"))
    directives, free_instr = parse_instructions(o.instructions)
    if directives.get("style"):
        profile = styles.load_profile(directives["style"])
    shared = root / "shared" if len(targets) > 1 else root
    sd = subdirs(shared)
    if o.reference_video:
        step(f"Analysing {len(o.reference_video)} reference video(s) for editing style")
        from framefoundry.reference import analyze as RA
        from framefoundry.reference import profile_builder as PB
        analyses, names = [], []
        for k, rv in enumerate(o.reference_video):
            analyses.append(RA.analyze(rv, sd["project_files"] / f"reference_{k + 1:02d}", cfg, transcribe=not o.fast_reference))
            names.append(os.path.basename(rv))
        profile = styles._merge(styles.BASE_PROFILE, PB.build(analyses, names, profile))
        profile["name"] = f"{profile.get('name', 'profile')}+reference"
        for n in profile.get("analysis", {}).get("notes", []):
            info(f"  style: {n}")
        if o.save_profile:
            p = styles.save_profile(profile, o.save_profile, overwrite=o.overwrite)
            info(f"style profile saved as {p}")
    if directives:
        apply_directives(profile, directives, notes_global)
    if o.no_subtitles:
        profile["subtitles"]["enabled"] = False
    if o.karaoke:
        profile["subtitles"]["karaoke"] = True
    import yaml
    (sd["project_files"] / "style_profile.yaml").write_text(
        yaml.safe_dump({k: v for k, v in profile.items() if not k.startswith("_")}, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ---- output geometry -------------------------------------------------------------------
    W, H = cfg.resolution(o.aspect_ratio, o.resolution)
    fps = int(o.fps or cfg.main["output"]["fps"])
    q = QUALITY[o.quality]

    # ---- assets ------------------------------------------------------------------------------
    step("Assets")
    from framefoundry.assets import manager as AM
    man = AM.scan(o.assets_dir or [], o.asset or [], o.logo, str(shared))
    for n in man["notes"]:
        warn(n)
    info(f"logo: {'yes' if man['logo'] else 'no'} · images: {len(man['images'])} · videos: {len(man['videos'])} · "
         f"cut-out stickers: {len(man['stickers'])} · fonts: {len(man['fonts'])}")

    # ---- website / screen recordings -------------------------------------------------------------
    recordings = {}
    if o.website:
        step(f"Recording the website {o.website} (real Chromium, read-only)")
        from framefoundry.capture import login as LG
        from framefoundry.capture import website as WB
        profile_path = str(LG.profile_dir(o.browser_profile)) if o.browser_profile else None
        if profile_path and not os.path.isdir(profile_path):
            raise FrameFoundryError(f"browser profile '{o.browser_profile}' not found — create it with "
                                    f"./framefoundry.sh login --url {o.website} --profile {o.browser_profile}")
        steps = WB.load_steps(o.website_steps, o.website)
        # footage needed by the lines that will show the website: tagged [web] lines, or the body lines
        web_lines = [L for L in src_lines if L.get("tag") == "web"] or \
            [L for L in src_lines if L.get("tag") is None and L["section"] not in ("hook", "cta")]
        need = sum(len(L["plain"].split()) for L in web_lines) / 2.6 * 1.25 + 3.0
        try:
            meta_rec = WB.record(o.website, str(sd["screen_recordings"]), cfg.main["browser"], steps, "rec_01", profile_path,
                                 o.private_text or [], tour_seconds=min(float(cfg.main["browser"]["max_seconds"]) - 5, need))
        except FrameFoundryError as e:
            if o.require_website:
                raise
            meta_rec = {"status": "error", "error": str(e)}
        WB.warn_if_problem(meta_rec)
        if meta_rec.get("src") and os.path.exists(meta_rec["src"]) and meta_rec.get("duration", 0) > 1.0 \
                and meta_rec.get("status") != "error":
            recordings["rec_01"] = {"src": meta_rec["src"], "css_w": meta_rec["css_w"], "css_h": meta_rec["css_h"],
                                    "duration": meta_rec["duration"], "marks": meta_rec["marks"], "cursor": meta_rec["cursor"],
                                    "clicks": meta_rec["clicks"], "usable_start": meta_rec.get("usable_start", 0.0),
                                    "title": o.website, "source": "playwright"}
            info(f"recorded {meta_rec['duration']:.1f}s of real website footage ({len(meta_rec['marks'])} marked steps, "
                 f"{len(meta_rec.get('skipped') or [])} skipped)")
        else:
            msg = f"website recording failed: {meta_rec.get('error') or meta_rec.get('status')}"
            if o.require_website:
                raise FrameFoundryError(msg)
            warn(msg + " — continuing without website footage (supply --screen-recording to use your own capture)")
            notes_global.append(msg)
    for k, sr in enumerate(o.screen_recording or [], 1):
        name, rec = AM.screen_recording_entry(sr, k)
        recordings[name] = rec
        info(f"uploaded screen recording {os.path.basename(sr)}: {rec['duration']:.1f}s")

    # ---- music -------------------------------------------------------------------------------------
    music = o.background_music
    if o.music_from_reference:
        step("Extracting the instrumental from the reference video (rights confirmed by you)")
        from framefoundry.audio.separation import separate
        music = separate(o.reference_video[0], str(sd["project_files"] / "reference_music"), want=("music",))["music"]
    if music:
        mi = media_info(music)
        if not mi["has_audio"]:
            raise FrameFoundryError(f"--background-music {music} has no audio stream")

    # ---- per language -------------------------------------------------------------------------------
    results = []
    for lang in targets:
        out = root / lang if len(targets) > 1 else root
        out.mkdir(parents=True, exist_ok=True)
        try:
            res = produce(o, cfg, lang, source_lang, det, src_lines, meta, raw, profile, man, recordings, music, out,
                          shared, W, H, fps, q, notes_global + [f"instructions (not interpreted): {x}" for x in free_instr],
                          multi=len(targets) > 1)
        except Exception as e:  # noqa: BLE001 - one failing language must not stop the others
            if len(targets) == 1:
                raise
            import traceback
            msg = str(e) if isinstance(e, FrameFoundryError) else f"{type(e).__name__}: {e}"
            if not isinstance(e, FrameFoundryError):
                from framefoundry.util import _LOG_FILE
                if _LOG_FILE:
                    _LOG_FILE.write(traceback.format_exc())
            warn(f"{lang}: {msg}")
            res = {"language": lang, "passed": False, "error": msg, "output_dir": str(out)}
        results.append(res)
    summary = {"framefoundry": __version__, "campaign": str(root), "seconds": round(time.time() - t_all, 1),
               "source_language": source_lang, "languages": results}
    if len(targets) > 1:
        jdump(summary, root / "summary.json")
    step("Done")
    for r in results:
        if r.get("plan_only"):
            info(f"{r['language']:9s} PLANNED (no render) {r['output_dir']}  ({r['duration']:.1f}s, {r['resolution']})")
        elif r.get("passed"):
            info(f"{r['language']:9s} OK   {r['video']}  ({r['duration']:.1f}s, {r['resolution']})")
        else:
            info(f"{r['language']:9s} FAIL {r.get('error') or 'quality check failed: ' + ', '.join(r.get('qa_errors', []))}")
    info(f"total time {time.time() - t_all:.0f}s · log: {root / 'generation.log'}")
    set_log_file(None)
    return 0 if all(r.get("passed") for r in results) else 3


def language_version(o, cfg, lang, source_lang, src_lines, meta):
    """-> (lines, method, notes) for one target language."""
    variant = (o.script_by_lang or {}).get(lang) or (P.find_variant(o.script, lang) if o.script else None)
    if variant:
        _, lines, _ = P.load_script(variant)
        return P.split_long(lines), "variant-file", [f"{lang} script supplied by you: {variant}"]
    if lang == source_lang:
        return src_lines, "source", []
    if D.compatible(lang, source_lang) and not (o.all_languages or o.convert_script):
        return src_lines, "source", [f"script is {source_lang}; kept exactly as written for the {lang} video "
                                     "(add --convert-script to rewrite it in the other script)"]
    b = _brand(o, meta)
    lines, method, notes = T.adapt(src_lines, source_lang, lang, cfg.language,
                                   protect=[b["name"], b["handle"], b["tagline"] if source_lang == "english" and False else ""])
    for n in notes:
        warn(f"{lang}: {n}")
    return P.split_long(lines), method, notes


def produce(o, cfg, lang, source_lang, det, src_lines, meta, raw, profile, man, recordings, music, out, shared,
            W, H, fps, q, notes, multi=False):
    t0 = time.time()
    step(f"[{lang}] script")
    lines, method, lnotes = language_version(o, cfg, lang, source_lang, src_lines, meta)
    notes = list(notes) + lnotes
    (out / "script.txt").write_text(P.lines_to_text(lines, meta), encoding="utf-8")
    det_final = D.detect("\n".join(L["plain"] for L in lines))
    report = {"requested": o.language, "all_languages": bool(o.all_languages), "target": lang,
              "source_language": source_lang, "source_variant": det["variant"], "source_script": det["script"],
              "method": method, "machine_translated": method == "machine_translation", "notes": lnotes,
              "final_text": {"language": det_final["language"], "variant": det_final["variant"], "script": det_final["script"],
                             "scores": det_final["scores"], "counts": det_final["counts"]},
              "sentences": [{"text": s["text"], "language": s["language"], "variant": s["variant"]} for s in det_final["sentences"]]}
    jdump(report, out / "language_report.json")
    pf = out / "project_files"
    pf.mkdir(exist_ok=True)

    # ---- narration --------------------------------------------------------------------------------
    whisper_lang = cfg.language["whisper_language"][lang]
    vo = (o.voiceover_by_lang or {}).get(lang) or (o.voiceover if (not multi or lang == source_lang) else None)
    from framefoundry.voice import engines as E
    spoken_lines = [{"id": L["id"], "spoken": L.get("tts") or E.tts_text(L, lang, cfg), "section": L["section"]} for L in lines]
    voice_wav, voice_info = None, {}
    if vo:
        step(f"[{lang}] uploaded narration {os.path.basename(vo)} -> word timing")
        from framefoundry.audio.voiceover import import_voiceover
        voice_wav, timing, rep = import_voiceover(vo, spoken_lines, cfg, str(out), whisper_lang, o.voice_processing)
        voice_info = {"source": "uploaded", "file": os.path.abspath(vo), "alignment": {k: v for k, v in rep.items() if k != "transcript"}}
        jdump(rep, pf / "voiceover_alignment.json")
    elif o.no_narration:
        step(f"[{lang}] no narration: timing from reading pace")
        timing = _timing_no_narration(lines, profile["rhythm"]["words_per_second"], o.duration)
        voice_info = {"source": "none"}
    else:
        engine = E.select_engine(cfg, o.voice_engine)
        if engine is None:
            raise FrameFoundryError("--voice-engine none needs --voiceover or --no-narration")
        step(f"[{lang}] voice generation ({E.ENGINES[engine]['title']}; {E.ENGINES[engine]['licence']})")
        ref, ref_text = None, None
        if o.voice_reference:
            from framefoundry.voice.reference import prepare
            ref, ref_text, _ = prepare(o.voice_reference, str(shared / "project_files" / "voice_reference"), cfg,
                                       transcribe_text=(engine == "omnivoice"), language=whisper_lang)
        res = E.synthesize([dict(L, tts=sl["spoken"]) for L, sl in zip(lines, spoken_lines)], lang, cfg, str(pf / "voice_takes"),
                           engine, ref, ref_text)
        jdump(res["qa"], pf / "voice_qa.json")
        from framefoundry.audio.voiceover import assemble
        voice_wav, timing = assemble(spoken_lines, res["takes"], cfg, str(out), lang, res["words"])
        voice_info = {"source": "generated", "engine": res["engine"], "title": res["title"], "licence": res["licence"],
                      "commercial_use": res["commercial"], "voice_reference": bool(ref), "language_id": res["language_id"],
                      "readback_failures": [k for k, r in res["qa"].items() if r.get("passed") is False],
                      "generation_seconds": res["seconds"]}
        if not res["commercial"]:
            notes.append("NON-COMMERCIAL: narration generated with CC-BY-NC weights (OmniVoice)")
    jdump(timing, pf / "timing.json")

    # ---- scene plan + edit --------------------------------------------------------------------------
    step(f"[{lang}] scene plan and edit ({W}x{H} @ {fps} fps)")
    stills = [i for i in man["images"] if not i["alpha"] or min(i["w"], i["h"]) >= 600]
    brand = _brand(o, meta)
    ctx = {"style": profile, "brand": {"name": brand["name"], "logo": man["logo"]},
           "recordings": [{"name": k, "title": v.get("title"), "steps": [m[0] for m in v.get("marks", [])]} for k, v in recordings.items()],
           "images": [{"id": i["id"], "name": i["name"]} for i in stills],
           "videos": [{"id": v["id"], "name": v["name"]} for v in man["videos"]]}
    scenes = planner.plan_scenes(lines, ctx)
    plan_doc = {"root": str(out), "output": {"w": W, "h": H, "fps": fps}, "style": profile, "scenes": scenes,
                "recordings": recordings, "assets": {**{i["id"]: i for i in stills}, **{v["id"]: v for v in man["videos"]}},
                "stickers": man["stickers"], "fonts": man["fonts"],
                "brand": {"name": brand["name"], "handle": brand["handle"], "tagline": brand["tagline"], "bg_word": brand["bg_word"],
                          "logo": man["logo"], "logo_icon": man["logo"]}}
    jdump(scenes, pf / "scene_plan.json")
    edl = editor.build(plan_doc, timing)
    if o.duration and edl["duration"] < o.duration - 0.5 and profile["structure"].get("end_card", True):
        extra = min(5.0, o.duration - edl["duration"])
        edl["clips"][-1]["end"] = round(edl["clips"][-1]["end"] + extra, 3)
        edl["duration"] = round(edl["duration"] + extra, 3)
        notes.append(f"end card held {extra:.1f}s longer to approach the requested {o.duration:.0f}s")
    elif o.duration and edl["duration"] > o.duration * 1.15:
        warn(f"the video is {edl['duration']:.1f}s — longer than the requested ~{o.duration:.0f}s (shorten the script)")
    edl["fade_out"] = cfg.main["output"]["fade_out"]
    jdump(edl, pf / "edl.json")
    kinds = {}
    for c in edl["clips"]:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    info(f"{len(edl['clips'])} clips ({', '.join(f'{v} {k}' for k, v in kinds.items())}), {len(edl['transitions'])} transitions, "
         f"{len(edl['subtitles'])} subtitle events, {edl['duration']:.2f}s")
    if o.plan_only:
        timeline.write(out / "scene_timeline.md", edl, scenes, timing, brand["name"] or out.name, lang, notes)
        info("--plan-only: stopping before mixing and rendering")
        return {"language": lang, "passed": True, "video": None, "duration": edl["duration"], "resolution": f"{W}x{H}",
                "output_dir": str(out), "plan_only": True}

    # ---- audio mix ---------------------------------------------------------------------------------
    step(f"[{lang}] audio mix")
    from framefoundry.audio.mix import mix
    final_audio, mix_info = mix(voice_wav, edl["duration"], str(out), cfg, music, profile.get("music"))
    if music and os.path.exists(out / "background_music.wav") is False:
        warn("background music could not be arranged")
    from framefoundry.audio.process import encode_aac_safe, loudness_stats
    mix_info["measured_wav"] = loudness_stats(final_audio)
    oc = cfg.main["output"]
    aac, mix_info["measured"], mix_info["aac_gain_db"] = encode_aac_safe(final_audio, str(pf / "final_audio.m4a"),
                                                                          oc["audio_bitrate"], cfg.main["audio"]["true_peak"],
                                                                          oc["audio_sample_rate"], mix_info.get("target_lufs"))
    info(f"final audio {mix_info['measured']['integrated_lufs']} LUFS, true peak {mix_info['measured']['true_peak_dbtp']} dBTP (AAC, verified)"
         + (f", music '{mix_info['music']}' ducked under the voice" if mix_info.get("music") and mix_info.get("ducking") else ""))

    # ---- subtitles, render, QA -------------------------------------------------------------------------
    from framefoundry.subtitles.export import write_all
    sub_info = write_all(edl, timing, scenes, str(out))
    step(f"[{lang}] rendering")
    from framefoundry.rendering.render import render_parallel
    partial = out / "final_video.partial.mp4"
    jobs = o.jobs or (None if cfg.main["rendering"]["jobs"] == "auto" else int(cfg.main["rendering"]["jobs"]))
    encoder = o.encoder or cfg.main["rendering"].get("hardware_encoder", "none")
    encoder = "libx264" if encoder in (None, "none", "cpu") else encoder
    render_parallel(str(pf / "edl.json"), aac, str(partial), jobs, o.crf or q["crf"], q["preset"], encoder,
                    cfg.main["rendering"].get("motion_blur", True), oc["audio_codec"], oc["audio_bitrate"], oc["audio_sample_rate"])
    step(f"[{lang}] quality check")
    from framefoundry.qa.check import check
    qa = check(str(partial), edl, {"target_lufs": profile.get("music", {}).get("target_lufs", cfg.main["audio"]["master_lufs"]),
                                   "output_dir": str(out)}, None)
    final = out / ("final_video.mp4" if qa["passed"] else "final_video.FAILED_QA.mp4")
    os.replace(partial, final)
    qa["video"] = str(final)
    qa["narration"] = voice_info
    jdump(qa, out / "qa_report.json")
    for c in qa["checks"]:
        info(f"  {'PASS' if c['ok'] else ('WARN' if c['severity'] == 'warning' else 'FAIL')}  {c['name']}: {c['detail']}")
    timeline.write(out / "scene_timeline.md", edl, scenes, timing, brand["name"] or out.name, lang, notes)
    manifest = {"framefoundry": __version__, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "language": lang,
                "inputs": {"script": os.path.abspath(o.script) if o.script else None, "website": o.website,
                           "logo": os.path.abspath(o.logo) if o.logo else None, "assets_dir": o.assets_dir,
                           "voiceover": os.path.abspath(vo) if vo else None, "background_music": os.path.abspath(music) if music else None,
                           "reference_videos": o.reference_video, "screen_recordings": o.screen_recording,
                           "style_profile": profile.get("name"), "aspect_ratio": o.aspect_ratio, "resolution": f"{W}x{H}", "fps": fps,
                           "quality": o.quality, "instructions": o.instructions},
                "language_method": method, "narration": voice_info, "audio": mix_info, "subtitles": sub_info,
                "recordings": {k: {"source": v.get("source"), "duration": v.get("duration")} for k, v in recordings.items()},
                "qa_passed": qa["passed"], "notes": notes, "versions": _versions(), "seconds": round(time.time() - t0, 1),
                "music_file_signature": file_sig(music) if music else None}
    jdump(manifest, out / "manifest.json")
    if not o.keep_intermediates and not cfg.main["output"].get("keep_intermediates", True):
        shutil.rmtree(pf / "voice_takes", ignore_errors=True)
    return {"language": lang, "passed": qa["passed"], "video": str(final), "duration": qa["duration"], "resolution": f"{W}x{H}",
            "qa_errors": qa["errors"], "qa_warnings": qa["warnings"], "output_dir": str(out), "method": method,
            "narration": voice_info.get("source"), "seconds": round(time.time() - t0, 1)}


def model_note():
    return str(ROOT / "models")
