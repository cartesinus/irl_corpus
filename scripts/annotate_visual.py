"""
Annotate visual scene in an MP4 video using Gemini.

Extracts frames at a reduced FPS, sends to Gemini, and outputs
JSON with per-segment scene annotations for these tiers:
  acoustic_scene, visual_context, speaker_visibility, topic

Usage:
    python scripts/annotate_visual.py data/my_clip.mp4
    python scripts/annotate_visual.py data/my_clip.mp4 --fps 1 -o out.json

    # Save the reduced video so you can resume if annotation fails:
    python scripts/annotate_visual.py data/my_clip.mp4 --reduced-video /tmp/clip_2fps.mp4 -o out.json
    # On retry, ffmpeg is skipped automatically if /tmp/clip_2fps.mp4 already exists:
    python scripts/annotate_visual.py data/my_clip.mp4 --reduced-video /tmp/clip_2fps.mp4 --model gemini-2.5-flash -o out.json

Requires:
    GEMINI_API_KEY env var (or GOOGLE_CLOUD_PROJECT + GOOGLE_APPLICATION_CREDENTIALS for Vertex AI)
    ffmpeg on PATH
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

from google import genai  # pylint: disable=import-error,no-name-in-module
from google.genai import types  # pylint: disable=import-error,no-name-in-module

VISUAL_TIERS = ["acoustic_scene", "visual_context", "speaker_visibility", "topic"]


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Annotate visual scene in video using Gemini"
    )
    parser.add_argument("video", help="Path to input MP4 file")
    parser.add_argument(
        "--fps",
        type=float,
        default=2.0,
        help="Frames per second for downsampled video sent to Gemini (default: 2)",
    )
    parser.add_argument(
        "--output", "-o", help="Output JSON file path (default: stdout)"
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model to use (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--annotations",
        default=None,
        help="Path to annotations JSON file "
        "(default: annotations-v0.1.0-20260510.json in annotations dir)",
    )
    parser.add_argument(
        "--reduced-video",
        default=None,
        help="Path to save (or reuse) the fps-reduced video. "
        "If the file already exists, the ffmpeg step is skipped (resume). "
        "If omitted, a temp file is used and deleted after annotation.",
    )
    return parser.parse_args()


def reduce_fps(video_path: str, fps: float, output_path: str):
    """Downsample video to the given fps using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"fps={fps}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")


def load_categories(annotations_path: str) -> dict:
    """Load annotation tier categories from the annotations JSON file."""
    with open(annotations_path, encoding="utf-8") as file:
        data = json.load(file)
    return data["tiers"]


def build_schema(categories: dict) -> dict:
    """Build a JSON schema for structured Gemini output."""
    segment_props = {
        "begin_time": {
            "type": "string",
            "description": "Segment start in MM:SS format (00:00 = video start)",
        },
        "end_time": {
            "type": "string",
            "description": "Segment end in MM:SS format",
        },
    }
    for tier in VISUAL_TIERS:
        if tier in categories:
            segment_props[tier] = {
                "type": "string",
                "enum": list(categories[tier]["values"].keys()),
            }

    return {
        "type": "object",
        "properties": {
            "annotations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": segment_props,
                    "required": ["begin_time", "end_time"] + VISUAL_TIERS,
                },
            }
        },
        "required": ["annotations"],
    }


def build_prompt(categories: dict) -> str:
    """Build the annotation prompt for Gemini."""
    lines = [
        "Analyze this video and produce scene-level annotations.",
        "Divide the video into contiguous segments where the scene type changes.",
        "Segments must cover the full video without gaps.",
        "For each segment provide begin_time and end_time in MM:SS format, "
        "where 00:00 is the video start.",
        "",
        "Annotate each segment with the following categories:",
        "",
    ]
    for tier in VISUAL_TIERS:
        if tier not in categories:
            continue
        info = categories[tier]
        lines.append(f"## {tier}")
        lines.append(info["description"])
        for val, desc in info["values"].items():
            lines.append(f"  {val}: {desc}")
        if "note" in info:
            lines.append(f"  Note: {info['note']}")
        lines.append("")

    lines.append(
        "Return a JSON object with an 'annotations' array. "
        "Each entry must have begin_time, end_time, and one label per category above."
    )
    return "\n".join(lines)


def annotate_video(video_path: str, model: str, categories: dict) -> dict:
    """Send video to Gemini and return structured scene annotations."""
    use_vertex = bool(
        os.getenv("GOOGLE_CLOUD_PROJECT")
        and os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )

    schema = build_schema(categories)
    prompt = build_prompt(categories)

    safety = [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
    ]

    if use_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        print("Using Vertex AI.", file=sys.stderr)
        with open(video_path, "rb") as file:
            video_data = file.read()
        contents = [
            types.Part.from_bytes(data=video_data, mime_type="video/mp4"),
            prompt,
        ]
    else:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        print("Uploading video to Gemini Files API...", file=sys.stderr)
        myfile = client.files.upload(file=video_path)
        file_id = myfile.name
        for _ in range(60):
            status = client.files.get(name=file_id)
            if status.state == "ACTIVE":
                break
            print(
                f"  Waiting for file to become ACTIVE (state: {status.state})...",
                file=sys.stderr,
            )
            time.sleep(2)
        else:
            raise RuntimeError(f"File {file_id} did not become ACTIVE in time")
        contents = [myfile, prompt]

    print("Sending to Gemini for annotation...", file=sys.stderr)
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            safety_settings=safety,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
            max_output_tokens=65536,
        ),
    )

    if response.text is None:
        # Structured output with strict enum schema can return None.
        # Log debug info and retry without response_schema.
        finish_reason = "unknown"
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason)
        feedback = str(getattr(response, "prompt_feedback", "none"))
        print(
            f"  [warn] Schema-constrained response was None "
            f"(finish_reason={finish_reason}, prompt_feedback={feedback}). "
            "Retrying without response_schema...",
            file=sys.stderr,
        )
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                safety_settings=safety,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=65536,
            ),
        )

    if response.text is None:
        finish_reason = "unknown"
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason)
        feedback = str(getattr(response, "prompt_feedback", "none"))
        raise RuntimeError(
            f"Gemini returned empty response after retry. "
            f"finish_reason={finish_reason}, prompt_feedback={feedback}"
        )

    try:
        return json.loads(response.text)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to parse Gemini response: {exc}\nRaw: {response.text[:500]}"
        ) from exc


def main():
    """Entry point: reduce fps, annotate with Gemini, write output."""
    args = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    annotations_path = args.annotations or os.path.join(
        project_root, "annotations", "annotations-v0.1.0-20260510.json"
    )

    if not os.path.exists(annotations_path):
        print(f"Annotations file not found: {annotations_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.video):
        print(f"Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    categories = load_categories(annotations_path)

    # Determine where the fps-reduced video lives and whether we should clean it up.
    if args.reduced_video:
        reduced_video = args.reduced_video
        cleanup = False
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        reduced_video = tmp.name
        tmp.close()
        cleanup = True

    try:
        if os.path.exists(reduced_video):
            print(f"Reusing existing reduced video: {reduced_video}", file=sys.stderr)
        else:
            print(f"Reducing video to {args.fps} fps via ffmpeg...", file=sys.stderr)
            reduce_fps(args.video, args.fps, reduced_video)
            print(f"  Saved reduced video: {reduced_video}", file=sys.stderr)

        result = annotate_video(reduced_video, args.model, categories)
    finally:
        if cleanup and os.path.exists(reduced_video):
            os.unlink(reduced_video)

    output = {
        "video": os.path.abspath(args.video),
        "fps_used": args.fps,
        "model": args.model,
        "annotations": result["annotations"],
    }

    json_str = json.dumps(output, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(json_str)
        print(f"Annotations written to {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
