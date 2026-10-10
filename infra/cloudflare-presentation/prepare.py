"""Stage only the standalone UI for cf 1.0.0-beta.14 static-asset deployment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[2]
PUBLIC_FILES = ("index.html", "styles.css", "app.js", "scenario.js", "evidence.js")
PROJECT = REPO / "build" / "cloudflare-presentation"
OUTPUT = PROJECT / ".cloudflare" / "output" / "v0"
WORKER_NAME = "memory-gateway-demo"


def main() -> None:
    # Validate the explicit inputs before replacing our generated output.
    source = REPO / "presentation"
    inputs = [source / name for name in PUBLIC_FILES]
    for path in inputs:
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Expected a regular public asset: {path.name}")
    if OUTPUT.exists():
        if OUTPUT.is_symlink():
            raise SystemExit("Refusing a symlink for the generated output directory")
        shutil.rmtree(OUTPUT)
    assets = OUTPUT / "workers" / "default" / "assets"
    assets.mkdir(parents=True)
    manifest = []
    for path in inputs:
        data = path.read_bytes()
        (assets / path.name).write_bytes(data)
        manifest.append({"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    # cf deploy --prebuilt reads this schema; no framework build or runtime code.
    (OUTPUT / "config.json").write_text(json.dumps({"buildContext": {"isPreview": False}}, indent=2) + "\n")
    (assets.parent / "worker.config.json").write_text(json.dumps({
        "name": WORKER_NAME,
        "compatibilityDate": "2026-10-09",
        "workersDev": True,
        "previewUrls": False,
        "assets": {"htmlHandling": "auto-trailing-slash", "notFoundHandling": "none"},
        "env": {},
    }, indent=2) + "\n")
    (PROJECT / "asset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"worker": WORKER_NAME, "project": str(PROJECT), "files": manifest}, indent=2))


if __name__ == "__main__":
    main()
