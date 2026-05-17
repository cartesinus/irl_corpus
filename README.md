# twitch-annotation-corpus

## Setup

Create a virtual environment with [uv](https://docs.astral.sh/uv/) and install dependencies:

```bash
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e .
```

Optional extras:

```bash
uv pip install -e ".[vad]"  # silero-vad, for add_vad_to_eaf.py
uv pip install -e ".[vis]"  # matplotlib, for wav_to_mel.py --png
uv pip install -e ".[all]"  # both of the above
```

`ffmpeg` must also be on your `PATH` (used by `annotate_visual.py`):

```bash
# Ubuntu/Debian
sudo apt install ffmpeg
```

Set your Gemini API key before running `annotate_visual.py`:

```bash
export GEMINI_API_KEY="your-key-here"
```

---

## Visual scene annotation

Annotate a video clip with `acoustic_scene`, `visual_context`, `speaker_visibility`, and `topic` using Gemini, then write the results into an EAF file.

**Requirements:** `GEMINI_API_KEY` env var (or `GOOGLE_CLOUD_PROJECT` + `GOOGLE_APPLICATION_CREDENTIALS` for Vertex AI), `ffmpeg` on PATH.

```bash
# 1. Annotate — outputs JSON (default 2 fps, prints to stdout)
python scripts/annotate_visual.py data/clip.mp4 -o annotations.json

# Optional flags: --fps 1, --model gemini-2.5-flash, --output file.json

# 2. Write annotations into EAF
python scripts/add_visual_annotations_to_eaf.py annotations.json data/clip.eaf
# If tiers already have data, the script will ask: [r]eplace / [a]dd / [s]kip
```

Category definitions: [`annotations-v0.1.0-20260510.json`](annotations-v0.1.0-20260510.json)
