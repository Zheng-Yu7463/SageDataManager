from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.dependencies import AGENT_ERROR_CODE_HEADER
from app.api.router import router
from app.core.config import settings
from app.domain.enums import AssetType
from app.domain.schemas import MAX_ASSET_DETAILS_BYTES, MAX_PUBLICATION_AUTHOR_LENGTH
from app.services.storage import (
    MAX_ARCHIVE_PATH_COMPONENT_BYTES,
    MAX_ARCHIVE_PATH_COMPONENT_LENGTH,
    MAX_ARCHIVE_RELATIVE_PATH_BYTES,
    MAX_ARCHIVE_RELATIVE_PATH_LENGTH,
)
from app.services.upload_directories import UPLOAD_DIRECTORY_OPTIONS

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_api_caching(request: Request, call_next):
    response = await call_next(request)
    if (
        request.url.path == settings.api_prefix
        or request.url.path.startswith(f"{settings.api_prefix}/")
    ) and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    agent_api_path = f"{settings.api_prefix}/agent"
    fallback_error_code = {
        416: "range_not_satisfiable",
        422: "request_invalid",
    }.get(response.status_code)
    if (
        fallback_error_code
        and (
            request.url.path == agent_api_path
            or request.url.path.startswith(f"{agent_api_path}/")
        )
        and AGENT_ERROR_CODE_HEADER not in response.headers
    ):
        response.headers[AGENT_ERROR_CODE_HEADER] = fallback_error_code
    return response


app.include_router(router, prefix=settings.api_prefix)

AGENT_PROTOCOL_VERSION = "1.0"
AGENT_DOCUMENT_VERSION = "2026-08-17.16"
AGENT_INSTRUCTIONS = (
    Path(__file__)
    .with_name("agent.md")
    .read_text(encoding="utf-8")
    .replace("{{PROTOCOL_VERSION}}", AGENT_PROTOCOL_VERSION)
    .replace("{{DOCUMENT_VERSION}}", AGENT_DOCUMENT_VERSION)
    .replace("{{MAXIMUM_FILE_SIZE_BYTES}}", str(settings.agent_upload_max_bytes))
    .replace(
        "{{MAXIMUM_UPLOAD_FILES_PER_TASK}}",
        str(settings.agent_upload_max_files_per_task),
    )
    .replace(
        "{{MAXIMUM_UPLOAD_TOTAL_BYTES}}",
        str(settings.agent_upload_max_total_bytes),
    )
    .replace(
        "{{MAXIMUM_PUBLICATION_AUTHOR_CHARACTERS}}",
        str(MAX_PUBLICATION_AUTHOR_LENGTH),
    )
    .replace("{{MAXIMUM_ASSET_DETAILS_BYTES}}", str(MAX_ASSET_DETAILS_BYTES))
)


@app.get("/agent.md", include_in_schema=False)
def agent_instructions() -> PlainTextResponse:
    return PlainTextResponse(
        AGENT_INSTRUCTIONS,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/.well-known/datamanager-agent.json", include_in_schema=False)
def agent_discovery() -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": AGENT_PROTOCOL_VERSION,
            "documentation_version": AGENT_DOCUMENT_VERSION,
            "name": settings.app_name,
            "instructions": "/agent.md",
            "openapi": "/api/openapi.json",
            "api_base": "/api/agent",
            "authentication": {
                "type": "http_bearer",
                "header": "Authorization",
            },
            "capabilities": [
                "asset_search",
                "asset_metadata",
                "file_read",
                "direct_upload",
                "upload_recovery",
                "upload_manifest_summary",
                "archive_finalize",
                "citation_export",
            ],
            "scopes": {
                "assets:read": ["GET /assets", "GET /assets/{asset_id}"],
                "files:read": ["GET /files/{file_id}/content"],
                "metadata:write": ["POST /assets", "PATCH /assets/{asset_id}"],
                "files:upload": [
                    "POST /uploads",
                    "GET /uploads/{upload_id}",
                    "DELETE /uploads/{upload_id}",
                    "PUT /uploads/{upload_id}/files/{relative_path}",
                ],
                "archive:finalize": ["POST /uploads/{upload_id}/finalize"],
                "citations:export": ["GET /assets/{asset_id}/citation/bibtex"],
            },
            "errors": {
                "code_header": AGENT_ERROR_CODE_HEADER,
                "codes": [
                    "agent_auth_required",
                    "agent_auth_invalid",
                    "agent_auth_unavailable",
                    "agent_scope_missing",
                    "asset_not_found",
                    "asset_slug_conflict",
                    "asset_metadata_conflict",
                    "asset_revision_conflict",
                    "file_not_found",
                    "file_preview_unavailable",
                    "file_unavailable",
                    "citation_incomplete",
                    "request_invalid",
                    "range_invalid",
                    "range_not_satisfiable",
                ],
            },
            "limits": {
                "default_page_size": 10,
                "maximum_page_size": 100,
                "maximum_file_size_bytes": settings.agent_upload_max_bytes,
                "maximum_upload_files_per_task": settings.agent_upload_max_files_per_task,
                "maximum_upload_total_bytes": settings.agent_upload_max_total_bytes,
                "maximum_publication_author_characters": (
                    MAX_PUBLICATION_AUTHOR_LENGTH
                ),
                "maximum_asset_details_bytes": MAX_ASSET_DETAILS_BYTES,
                "maximum_upload_path_characters": MAX_ARCHIVE_RELATIVE_PATH_LENGTH,
                "maximum_upload_path_bytes": MAX_ARCHIVE_RELATIVE_PATH_BYTES,
                "maximum_upload_path_component_characters": (MAX_ARCHIVE_PATH_COMPONENT_LENGTH),
                "maximum_upload_path_component_bytes": MAX_ARCHIVE_PATH_COMPONENT_BYTES,
                "upload_path_encoding": "percent-encoded UTF-8 segments",
            },
            "uploads": {
                "task_token_header": "X-Sage-Upload-Token",
                "checksum_header": "X-Sage-Content-SHA256",
                "error_code_header": AGENT_ERROR_CODE_HEADER,
                "retry_after_header": "Retry-After",
                "status_checksum_query_parameter": "include_checksums",
                "manifest_summary_fields": ["expected_file_count", "expected_total_size"],
                "manifest_summary_required_for_new_clients": True,
                "error_codes": [
                    "invalid_checksum",
                    "invalid_content_length",
                    "upload_busy",
                    "upload_cancel_failed",
                    "upload_conflict",
                    "upload_credentials_invalid",
                    "upload_invalid",
                    "upload_manifest_mismatch",
                    "upload_manifest_too_large",
                    "upload_not_ready",
                    "upload_status_unavailable",
                    "upload_storage_unavailable",
                    "upload_target_invalid",
                    "upload_token_missing",
                    "upload_too_large",
                ],
                "status_values": ["waiting", "ready", "completed", "cancelled"],
                "empty_files_allowed": False,
                "file_put_idempotency": {
                    "requires_checksum": True,
                    "match_fields": ["relative_path", "checksum_sha256"],
                    "content_length_must_match_when_present": True,
                    "overwrites_existing_file": False,
                },
            },
            "asset_types": [asset_type.value for asset_type in AssetType],
            "upload_directories": {
                asset_type.value: [name for name, _ in UPLOAD_DIRECTORY_OPTIONS[asset_type]]
                for asset_type in AssetType
            },
        },
        headers={"Cache-Control": "no-cache"},
    )
