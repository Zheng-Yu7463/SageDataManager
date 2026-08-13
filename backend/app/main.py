from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.router import router
from app.core.config import settings

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
app.include_router(router, prefix=settings.api_prefix)


@app.get("/agent.md", include_in_schema=False)
def agent_instructions() -> PlainTextResponse:
    return PlainTextResponse(
        """# DataManager Agent Interface

This DataManager exposes a scoped HTTP API for authorized AI agents.

## Discovery

- OpenAPI schema: `/api/openapi.json`
- Agent identity: `GET /api/agent/me`
- Agent endpoints: `/api/agent/*`

## Authentication

Send a personal access token in every agent request:

`Authorization: Bearer sdm_pat_<public-id>_<secret>`

Tokens are created by a human administrator in System Settings.
Never place a token in this document, source code, URLs, logs, or asset metadata.

## Required workflow

1. Search before creating: `GET /api/agent/assets?query=...`.
2. Read the matching record: `GET /api/agent/assets/{asset_id}`.
3. Update stale metadata in place: `PATCH /api/agent/assets/{asset_id}`.
   The `details` field replaces the complete object; read it first and preserve
   fields that are not changing.
4. Create metadata only when no matching asset exists: `POST /api/agent/assets`.
5. Create an isolated upload task: `POST /api/agent/uploads`.
6. Upload each file with `PUT` to the returned `file_upload_url_template`.
   Send the upload token in `X-Sage-Upload-Token` and use the same personal
   access token that created the task.
7. Finalize after every upload succeeds. `POST` to `finalize_url`
   with `{"upload_token":"..."}`.
   Finalization is idempotent: after a lost response, retry it with the same
   upload ID and tokens. A completed task rejects additional file uploads.
8. On a file conflict, do not overwrite or rename the file without user direction.

## Catalogue policy

- `paper`: work authored by the lab, including submissions and publications.
- `literature`: external papers, preprints, journal articles, annotations, and notes.
- Preserve official source identifiers and BibTeX metadata when available.
- Do not infer unknown citation fields.

## Safety

- Never request `assets:archive` or administrative settings.
  These capabilities are unavailable to agent tokens.
- Upload paths must be relative to the task and must not contain `..`.
- File conflicts, symlinks, and invalid paths are rejected before formal archival.
- All agent actions are attributed to the human account and token name.
""",
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/.well-known/datamanager-agent.json", include_in_schema=False)
def agent_discovery() -> JSONResponse:
    return JSONResponse(
        {
            "name": settings.app_name,
            "instructions": "/agent.md",
            "openapi": "/api/openapi.json",
            "api_base": "/api/agent",
            "authentication": "bearer",
        }
    )
