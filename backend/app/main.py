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
        r"""# DataManager Agent Interface

Protocol version: 1.0
Document version: 2026-08-15

This instance exposes a scoped HTTP API for authorized file-management agents.
Use paths relative to the instance origin. The OpenAPI schema is authoritative
for request and response shapes.

## Discovery and authentication

- Discovery: `GET /.well-known/datamanager-agent.json`
- OpenAPI: `GET /api/openapi.json`
- Identity and granted scopes: `GET /api/agent/me`
- API base: `/api/agent`

Send the personal access token on every request:

`Authorization: Bearer sdm_pat_<public-id>_<secret>`

Never put a token in a URL, log, source file, asset metadata, or this document.
Upload tasks also return a short-lived `upload_token`. It must be supplied as
`X-Sage-Upload-Token` for file upload, status, and cancellation, and in the
JSON body for finalization. An upload task is bound to the PAT that created it.

## Scope matrix

| Scope | Operations |
| --- | --- |
| `assets:read` | Search/list assets and read full asset details |
| `files:read` | Preview or download indexed files |
| `metadata:write` | Create assets and update asset metadata |
| `files:upload` | Create, inspect, cancel, and upload files to isolated tasks |
| `archive:finalize` | Validate and move a staged task into the formal archive |
| `citations:export` | Export one publication as BibTeX |

Agent tokens cannot archive assets, change settings, scan storage, manage users,
or manage access tokens. There is no `assets:archive` Agent scope.

## Catalogue workflow

1. Search before creating:
   `GET /api/agent/assets?query=<encoded-query>&page=1&page_size=10`.
2. Continue pagination until `page * page_size >= total`. List records are
   compact by design and omit full `details`, summaries, owners, and file lists.
3. Read a candidate with `GET /api/agent/assets/{asset_id}`.
4. Create only when no existing record matches: `POST /api/agent/assets`.
5. To update, copy the latest detail response's `updated_at` value into the
   `X-Sage-Asset-Revision` header on
   `PATCH /api/agent/assets/{asset_id}`.
6. A PATCH `details` value replaces the entire details object. Preserve every
   existing field that is not intentionally changing. Omitted top-level fields
   remain unchanged.
7. On `409` stale-revision conflict, read the asset again, merge deliberately,
   and retry once with the new revision. Never blindly overwrite concurrent work.

Asset types are `paper`, `dataset`, `literature`, `project`, and `model`.
Use `paper` for lab-authored work and `literature` for external publications.
Preserve official identifiers and citation data. Do not infer unknown fields.

## Reading files

Asset detail responses contain indexed file IDs. Read one with:

`GET /api/agent/files/{file_id}/content?mode=download`

Use `mode=preview` only for supported text, PDF, JSON, YAML, CSV, and image
types. The endpoint streams bytes, supports `Range`, and returns
`Cache-Control: private, no-store`.

## Upload workflow

1. Create a task with `POST /api/agent/uploads`:
   `{"asset_id":"<uuid>","target_subdirectory":"original"}`.
2. The first directory component must be allowed for the asset type:

| Type | Allowed first component |
| --- | --- |
| `paper` | `manuscript`, `supplementary`, `source`, `reviews` |
| `dataset` | `raw`, `processed`, `documentation`, `scripts` |
| `literature` | `original`, `annotations`, `notes` |
| `project` | `documentation`, `code`, `data`, `outputs` |
| `model` | `weights`, `checkpoints`, `configs`, `evaluation` |

3. Upload every non-empty file with `PUT` to
   `file_upload_url_template`. Percent-encode each `relative_path` segment
   as UTF-8 while preserving `/` separators. Paths must be relative, may not
   contain `.` or `..` components, and must not use system-reserved names.
4. Send `X-Sage-Upload-Token`. When available, also send the lowercase
   64-character SHA-256 in `X-Sage-Content-SHA256`. The response contains the
   checksum calculated from received bytes.
5. Maximum size is 500,000,000 bytes per file. A `413` response means the
   whole file was rejected and no partial file remains.
6. Recover after interruption with `GET` on `status_url`, sending the PAT
   and `X-Sage-Upload-Token`. States are `waiting`, `ready`, `completed`,
   and `cancelled`.
7. Cancel an unused task with `DELETE` on `cancel_url`, using the same two
   credentials. Cancellation is idempotent and safely removes staged files.
8. After all uploads succeed, `POST` to `finalize_url` with
   `{"upload_token":"<upload-token>"}`. Finalization is idempotent after a
   lost response. It stores SHA-256 values and rejects duplicate content.
9. Never overwrite or silently rename a conflicting path. Ask the user how to
   resolve a `409` file conflict.

The task's `expires_at` is authoritative. Do not start or retry work after it.

## Error handling

- `400`: malformed header or request; correct it before retrying.
- `401`: PAT or required upload header is missing/invalid; stop and obtain
  valid credentials.
- `403`: missing scope or credential/task mismatch; do not broaden scopes
  without the administrator's approval.
- `404`: resource is absent or its asset is archived; search again.
- `409`: state, revision, metadata, path, checksum, or content conflict;
  inspect the detail and resolve the cause before retrying.
- `413`: file exceeds the configured limit; do not retry the same payload.
- `422`: request does not match the schema; correct fields and types.
- `429` or `5xx`: retry idempotent reads with bounded exponential backoff.
  Retry uploads only after checking task status. Retry finalization with the
  same upload ID and tokens.

## Minimal examples

Assume `BASE_URL`, `SAGE_TOKEN`, `ASSET_ID`, and `FILE_ID` are already
set in the process environment.

```sh
curl -fsS "$BASE_URL/api/agent/me" \
  -H "Authorization: Bearer $SAGE_TOKEN"

curl -fsS "$BASE_URL/api/agent/assets?query=example&page=1&page_size=10" \
  -H "Authorization: Bearer $SAGE_TOKEN"

curl -fsS "$BASE_URL/api/agent/assets/$ASSET_ID" \
  -H "Authorization: Bearer $SAGE_TOKEN"

curl -fSL "$BASE_URL/api/agent/files/$FILE_ID/content?mode=download" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -o downloaded-file
```

All Agent mutations and file reads are attributed to the human account and PAT
name in the activity log.
""",
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/.well-known/datamanager-agent.json", include_in_schema=False)
def agent_discovery() -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": "1.0",
            "documentation_version": "2026-08-15",
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
            "asset_types": ["paper", "dataset", "literature", "project", "model"],
            "upload_directories": {
                "paper": ["manuscript", "supplementary", "source", "reviews"],
                "dataset": ["raw", "processed", "documentation", "scripts"],
                "literature": ["original", "annotations", "notes"],
                "project": ["documentation", "code", "data", "outputs"],
                "model": ["weights", "checkpoints", "configs", "evaluation"],
            },
        }
    )
