"""
End-to-end proof for PR #637: high-precision TIFF -> vision LLM.

Builds a 16-bit grayscale (I;16) TIFF — the exact Pillow mode whose
conversion was broken — containing a hard-to-guess marker string, runs it
through the *real* Atlas production code path
(handle_session_files -> _normalize_vision_image_for_llm ->
_convert_tiff_to_png_b64 -> _build_vision_user_message), then sends the
resulting vision message to a real vision-capable model (OpenAI gpt-4o).

If the model reads back the marker string, the whole pipeline works on a
genuine high-precision TIFF, end to end.

Run from repo root:  python scripts/_tiff_vision_e2e_proof.py
"""

import asyncio
import base64
import json
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from atlas.application.chat.preprocessors.message_builder import _build_vision_user_message
from atlas.application.chat.utilities.file_processor import handle_session_files
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

MARKER_TEXT = "ATLAS-TIFF-7391"
PROMPT = (
    "This image is a scanned document. Read the text exactly as it appears "
    "and report it verbatim. Then describe the shape drawn below the text."
)
OUT_DIR = "scripts/_e2e_artifacts"


def build_high_precision_tiff(path_png_preview: str) -> bytes:
    """Render text + a shape, then store as a real 16-bit (I;16) TIFF."""
    # Draw on an 8-bit canvas first (ImageDraw text is simplest there).
    w, h = 640, 240
    canvas = Image.new("L", (w, h), 0)  # black background
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
        small = ImageFont.truetype("DejaVuSans.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
        small = ImageFont.load_default()
    draw.text((40, 40), MARKER_TEXT, fill=255, font=font)          # white text
    draw.text((40, 110), "high-precision tiff", fill=200, font=small)
    # A distinctive shape below the text: a hollow triangle.
    draw.polygon([(320, 180), (250, 230), (390, 230)], outline=255, width=4)

    # Promote to a genuine 16-bit single-channel image (the path that broke):
    # spread the 8-bit values across the full 16-bit range.
    tiff_16 = Image.new("I;16", (w, h))
    tiff_16.putdata([px * 257 for px in canvas.getdata()])  # 0..255 -> 0..65535
    assert tiff_16.mode == "I;16"

    buf = BytesIO()
    tiff_16.save(buf, format="TIFF")
    tiff_bytes = buf.getvalue()

    # Save a human-viewable preview of what the source encodes (8-bit).
    canvas.save(path_png_preview, format="PNG")
    return tiff_bytes


async def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    tiff_bytes = build_high_precision_tiff(f"{OUT_DIR}/source_preview.png")
    tiff_b64 = base64.b64encode(tiff_bytes).decode()
    with open(f"{OUT_DIR}/sample.tiff", "wb") as fh:
        fh.write(tiff_bytes)

    # Sanity: confirm the source really is a 16-bit TIFF.
    src_mode = Image.open(BytesIO(tiff_bytes)).mode
    print(f"[1] Built source TIFF: {len(tiff_bytes)} bytes, PIL mode={src_mode!r}")
    assert src_mode in {"I;16", "I;16B", "I;16L", "I", "F"}, src_mode

    # --- Real production path: ingest as a vision upload ---------------------
    fm = FileManager(s3_client=MockS3StorageClient())
    context = await handle_session_files(
        session_context={},
        user_email="proof@example.com",
        files_map={"scan.tiff": {"content": tiff_b64, "extractMode": "none"}},
        file_manager=fm,
        model_supports_vision=True,
    )
    file_ref = context["files"]["scan.tiff"]
    img_b64 = file_ref.get("image_b64")
    img_mime = file_ref.get("image_mime_type")
    assert img_mime == "image/png", f"expected png, got {img_mime}"
    assert img_b64 and base64.b64decode(img_b64).startswith(b"\x89PNG\r\n\x1a\n")
    converted_png = base64.b64decode(img_b64)
    with open(f"{OUT_DIR}/converted_for_vision.png", "wb") as fh:
        fh.write(converted_png)
    print(f"[2] handle_session_files produced {img_mime}, "
          f"{len(converted_png)} bytes PNG (saved converted_for_vision.png)")

    # --- Real production path: build the multimodal vision message ----------
    vision_msg = _build_vision_user_message(
        PROMPT, [{"image_b64": img_b64, "image_mime_type": img_mime}]
    )
    assert vision_msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    print("[3] Built multimodal vision message (OpenAI image_url format)")

    # --- Send to a real vision-capable model --------------------------------
    # The OPENAI_API_KEY in this environment is an OpenRouter key (sk-or-v1).
    # OpenRouter is OpenAI-compatible, so the exact OpenAI image_url payload the
    # app builds is sent unchanged to a real vision model.
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: no OpenRouter/OpenAI API key set; cannot run live model call")
        return 2

    from openai import OpenAI
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)
    model = os.environ.get("PROOF_MODEL", "openai/gpt-4o")
    resp = client.chat.completions.create(
        model=model,
        messages=[vision_msg],
        max_tokens=300,
        temperature=0,
    )
    answer = resp.choices[0].message.content
    print(f"[4] {model} response:\n{answer}\n")

    passed = MARKER_TEXT in (answer or "")
    result = {
        "model": model,
        "source_tiff_pil_mode": src_mode,
        "source_tiff_bytes": len(tiff_bytes),
        "converted_png_bytes": len(converted_png),
        "converted_mime": img_mime,
        "marker_in_image": MARKER_TEXT,
        "model_response": answer,
        "marker_read_back_by_model": passed,
    }
    with open(f"{OUT_DIR}/result.json", "w") as fh:
        json.dump(result, fh, indent=2)

    print(f"[5] PASS={passed} (model {'READ' if passed else 'did NOT read'} "
          f"the marker {MARKER_TEXT!r})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
