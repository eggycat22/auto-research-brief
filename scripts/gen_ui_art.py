"""Generate reviewed UI stills via PhanRouter Seedream. Not part of the daily job."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402

ART = ROOT / "backend" / "app" / "static" / "art"

SHOTS = {
    "mark.jpg": "Fill the entire square frame with a sage-green cloth notebook cover, spine on the left edge, rounded corners, tight crop, almost no background, pale cream only as a sliver if needed, flat editorial product photo, no text, no letters, no watermark",
    "empty-today.jpg": "Calm editorial still life, pale cream paper background, one sage-green notebook in the center, empty desk, soft daylight, flat illustration, no people, no text, no watermark",
    "empty-inbox.jpg": "Pale cream paper background, empty sage-green paper tray with a single blank card, minimal flat illustration, generous negative space, no text, no watermark",
}


def main() -> None:
    import time
    import httpx

    p = argparse.ArgumentParser()
    p.add_argument("--key-file", type=Path, default=None)
    p.add_argument("--only", choices=list(SHOTS), nargs="*")
    args = p.parse_args()
    key_file = args.key_file
    if key_file is None and (settings.image_api_key_file or "").strip():
        key_file = Path(settings.image_api_key_file)
    if key_file is None or not key_file.is_file():
        fallback = ROOT / "_api_docs_extract"
        keys = list(fallback.rglob("phanrouter.key")) if fallback.exists() else []
        if not keys:
            raise SystemExit("Set IMAGE_API_KEY_FILE or keep the handover key file locally.")
        key_file = keys[0]
    key = key_file.read_text(encoding="utf-8").strip()
    ART.mkdir(parents=True, exist_ok=True)
    names = args.only or list(SHOTS)
    with httpx.Client(timeout=httpx.Timeout(180, connect=20)) as client:
        for name in names:
            prompt = SHOTS[name]
            payload = {
                "model": settings.image_api_model,
                "prompt": prompt,
                "n": 1,
                "size": "2048x2048",
                "watermark": False,
            }
            start = time.monotonic()
            r = client.post(
                settings.image_api_base_url.rstrip("/") + "/v1/images/generations",
                headers={"Authorization": "Bearer " + key},
                json=payload,
            )
            print("name=", name, "status=", r.status_code, "seconds=", round(time.monotonic() - start, 2))
            if not r.is_success:
                raise SystemExit("generation failed")
            url = r.json()["data"][0]["url"]
            img = client.get(url, follow_redirects=True)
            if not img.is_success:
                raise SystemExit("download failed")
            path = ART / name
            path.write_bytes(img.content)
            print("saved=", path)


if __name__ == "__main__":
    main()
