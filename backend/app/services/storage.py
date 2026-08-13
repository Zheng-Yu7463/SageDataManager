from pathlib import Path, PurePath

UPLOAD_STAGING_DIRECTORY = ".uploads"


def is_internal_storage_path(path: PurePath) -> bool:
    return bool(path.parts) and path.parts[0] == UPLOAD_STAGING_DIRECTORY


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".doc", ".docx", ".md", ".txt", ".tex"}:
        return "document"
    if suffix in {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".nc"}:
        return "data"
    if suffix in {".pt", ".pth", ".bin", ".safetensors", ".ckpt"}:
        return "model-weight"
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
        return "image"
    return "other"
