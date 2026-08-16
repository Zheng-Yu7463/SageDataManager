from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.router import router
from app.core.config import settings
from app.domain.enums import AssetType
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
    return response


app.include_router(router, prefix=settings.api_prefix)

AGENT_PROTOCOL_VERSION = "1.0"
AGENT_DOCUMENT_VERSION = "2026-08-17.1"
AGENT_INSTRUCTIONS = (
    Path(__file__)
    .with_name("agent.md")
    .read_text(encoding="utf-8")
    .replace("{{PROTOCOL_VERSION}}", AGENT_PROTOCOL_VERSION)
    .replace("{{DOCUMENT_VERSION}}", AGENT_DOCUMENT_VERSION)
    .replace("{{MAXIMUM_FILE_SIZE_BYTES}}", str(settings.agent_upload_max_bytes))
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
            "limits": {
                "default_page_size": 10,
                "maximum_page_size": 100,
                "maximum_file_size_bytes": settings.agent_upload_max_bytes,
                "upload_path_encoding": "percent-encoded UTF-8 segments",
            },
            "asset_types": [asset_type.value for asset_type in AssetType],
            "upload_directories": {
                asset_type.value: [name for name, _ in UPLOAD_DIRECTORY_OPTIONS[asset_type]]
                for asset_type in AssetType
            },
        },
        headers={"Cache-Control": "no-cache"},
    )
