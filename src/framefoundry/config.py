"""Configuration: config/config.yaml, config/voice_config.yaml, config/language_config.yaml.

Values are merged over built-in defaults, so a partial or older config file still works. Everything
machine-specific (model cache, tools) resolves relative to the FrameFoundry folder unless an absolute
path is configured. Environment variables for model caches are set here, before any ML import.
"""
import copy
import os
from pathlib import Path

import yaml

from framefoundry.util import ROOT, FrameFoundryError

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}

DEFAULTS = {
    "output": {"directory": "output", "aspect_ratio": "16:9", "fps": 30, "crf": 18, "preset": "medium",
               "video_codec": "libx264", "pix_fmt": "yuv420p", "audio_codec": "aac", "audio_bitrate": "256k",
               "audio_sample_rate": 48000, "fade_out": 0.45, "keep_intermediates": True},
    "style": {"default_profile": "generic_promo"},
    "rendering": {"jobs": "auto", "motion_blur": True, "hardware_encoder": "none"},
    "audio": {"narration_lufs": -16.0, "master_lufs": -14.0, "true_peak": -1.0, "music_bed_db": -5.0,
              "ducking": True, "noise_reduction": False, "trim_silence": True, "music_fade_in": 0.03,
              "music_fade_out": 1.5, "loop_music": True},
    "alignment": {"backend": "whisper", "whisper_model": "large-v3-turbo", "device": "cpu", "compute_type": "int8",
                  "beam_size": 5},
    "browser": {"viewport": [1920, 1080], "device_scale_factor": 1.5, "max_seconds": 60, "allow_risky_actions": False,
                "private_strings": [], "navigation_timeout": 45},
    "paths": {"models": "models", "tmp": "tmp", "logs": "logs", "profiles": "templates/profiles",
              "fonts": "assets/fonts"},
}

VOICE_DEFAULTS = {
    "engine": "auto",
    "allow_non_commercial_models": False,
    "chatterbox": {"model": "v3", "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 7,
                   "device": "cpu", "max_retries": 2},
    "omnivoice": {"steps": 32, "guidance_scale": 2.0, "seed": 0, "device": "cpu"},
    "reference": {"min_seconds": 5.0, "max_seconds": 11.0, "separate_music": "auto"},
    "assembly": {"line_gap": 0.12, "section_gap": 0.24, "lead_in": 0.15, "tail": 0.25},
    "qa": {"asr_readback": True, "max_char_error": 0.35},
}

LANGUAGE_DEFAULTS = {
    "default": "auto",
    "all_languages": ["english", "hindi", "hinglish"],
    "whisper_language": {"english": "en", "hindi": "hi", "hinglish": "hi"},
    "tts_language": {"english": "en", "hindi": "hi", "hinglish": "hi"},
    "hinglish": {"tts_hindi_words": "devanagari", "subtitle_script": "roman"},
    "hindi": {"subtitle_script": "devanagari"},
    "translation": {"backend": "marian",
                    "models": {"english-hindi": "Helsinki-NLP/opus-mt-en-hi",
                               "hindi-english": "Helsinki-NLP/opus-mt-hi-en"}},
    "detection": {"hinglish_min_ratio": 0.2},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path):
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise FrameFoundryError(f"invalid YAML in {path}: {e}") from None
    if not isinstance(data, dict):
        raise FrameFoundryError(f"{path} must contain a mapping at the top level")
    return data


class Config:
    def __init__(self, config_dir=None):
        d = Path(config_dir) if config_dir else ROOT / "config"
        self.dir = d
        self.main = _merge(DEFAULTS, _load_yaml(d / "config.yaml"))
        self.voice = _merge(VOICE_DEFAULTS, _load_yaml(d / "voice_config.yaml"))
        self.language = _merge(LANGUAGE_DEFAULTS, _load_yaml(d / "language_config.yaml"))

    def path(self, key):
        p = Path(os.path.expanduser(str(self.main["paths"][key])))
        return p if p.is_absolute() else ROOT / p

    def output_dir(self):
        p = Path(os.path.expanduser(str(self.main["output"]["directory"])))
        return p if p.is_absolute() else ROOT / p

    def resolution(self, aspect, resolution=None):
        if resolution:
            try:
                w, h = (int(x) for x in str(resolution).lower().replace("×", "x").split("x"))
            except ValueError:
                raise FrameFoundryError(f"invalid --resolution '{resolution}' (use WIDTHxHEIGHT, e.g. 1920x1080)") from None
            if w < 320 or h < 320 or w > 7680 or h > 7680:
                raise FrameFoundryError("resolution must be between 320 and 7680 pixels per side")
            return w // 2 * 2, h // 2 * 2
        if aspect not in ASPECTS:
            raise FrameFoundryError(f"unsupported --aspect-ratio '{aspect}' (supported: {', '.join(ASPECTS)}); "
                                    "use --resolution WIDTHxHEIGHT for anything else")
        return ASPECTS[aspect]

    def apply_environment(self):
        """Keep every model download inside the configured models folder (no global caches)."""
        models = self.path("models")
        models.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(models / "huggingface"))
        os.environ.setdefault("TORCH_HOME", str(models / "torch"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        bin_dir = ROOT / ".framefoundry" / "bin"
        if bin_dir.is_dir() and str(bin_dir) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


_CFG = None


def get_config(config_dir=None):
    """Configuration singleton. FRAMEFOUNDRY_CONFIG_DIR selects another folder of the three YAML files."""
    global _CFG
    config_dir = config_dir or os.environ.get("FRAMEFOUNDRY_CONFIG_DIR")
    if _CFG is None or (config_dir and str(_CFG.dir) != str(config_dir)):
        _CFG = Config(config_dir)
    return _CFG
