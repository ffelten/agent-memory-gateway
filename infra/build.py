"""Stage only Lambda source and dependencies, then perform a SAM build."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def stage_source(root: Path, destination: Path) -> None:
    """Use an allowlist so local configs, credentials, tests, and demos stay out."""
    required = (
        "gateway/runtime.py",
        "gateway/senso_bridge.py",
        "analytics/worker.py",
        "adapters/senso_adapter.py",
        "requirements.txt",
        "infra/template.yaml",
    )
    for relative in required:
        if not (root / relative).is_file():
            raise SystemExit(f"Missing required deployment source: {relative}")
    files = [root / relative for relative in required]
    for package in ("gateway", "analytics"):
        files.extend(
            path for path in (root / package).rglob("*.py")
            if not path.name.startswith("test_") and "tests" not in path.parts
        )
    adapter_init = root / "adapters/__init__.py"
    if adapter_init.is_file():
        files.append(adapter_init)
    for source in set(files):
        if source.is_symlink():
            raise SystemExit("Deployment sources must not be symbolic links")
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-container", action="store_true",
        help="Use local Python 3.12 for pure-Python dependencies when Docker is unavailable",
    )
    arguments = parser.parse_args()
    if shutil.which("sam") is None:
        raise SystemExit("Install the AWS SAM CLI before building")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="memory-gateway-sam-") as staging:
        staged = Path(staging)
        stage_source(root, staged)
        command = ["sam", "build"]
        if not arguments.no_container:
            command.append("--use-container")
        command.extend(
            [
                "--template-file", str(staged / "infra/template.yaml"),
                "--build-dir", str(root / "build/sam"),
            ]
        )
        subprocess.run(
            command,
            cwd=staged,
            check=True,
        )


if __name__ == "__main__":
    main()
