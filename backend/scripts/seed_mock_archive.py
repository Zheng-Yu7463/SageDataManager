from __future__ import annotations

import argparse
from pathlib import Path

MOCK_FILES = {
    "paper/sage-r1-scientific-qa/camera-ready/manuscript.pdf": b"%PDF-1.4\nSAGE mock paper\n",
    "paper/sage-r1-scientific-qa/camera-ready/rebuttal.md": b"# SAGE-R1 rebuttal\n",
    "dataset/climatebench-v2/v2.1/manifest.json": b'{"name": "ClimateBench v2.1", "mock": true}\n',
    "dataset/climatebench-v2/v2.1/observations.csv": b"timestamp,temperature\n2026-01-01,18.4\n",
    "literature/transformer-survey/notes/reading-notes.md": b"# Transformer survey notes\n",
    "project/multimodal-understanding/README.md": b"# Multimodal understanding\n",
    "model/sage-vision-7b/v1.1/config.json": b'{"model": "SAGE-Vision-7B", "mock": true}\n',
    "model/sage-vision-7b/v1.1/model.safetensors": b"SAGE MOCK WEIGHTS\n",
    "incoming/unclaimed-field-notes.txt": b"Mock file awaiting asset ownership.\n",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a safe mock SAGE archive tree.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "sample-archive",
    )
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    if root.name != "sample-archive":
        parser.error("只允许写入名为 sample-archive 的模拟目录。")

    for relative_path, content in MOCK_FILES.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    print(f"Created {len(MOCK_FILES)} mock files in {root}.")


if __name__ == "__main__":
    main()
