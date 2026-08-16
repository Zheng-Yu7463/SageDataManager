# Sage Data Manager Agent Interface

Protocol version: {{PROTOCOL_VERSION}}
Document version: {{DOCUMENT_VERSION}}

This instance exposes a scoped HTTP API for authorized research-asset and file-management
agents. Read this document before acting. Use paths relative to the instance origin. Fetch
`/api/openapi.json` when exact request or response shapes matter; OpenAPI is authoritative if
it differs from an example in this guide.

## 1. Bootstrap and authentication

1. Read `GET /.well-known/datamanager-agent.json`.
2. Read `GET /api/openapi.json` when planning a mutation or handling an unfamiliar response.
3. Call `GET /api/agent/me` and verify the human account, credential name, granted scopes, and
   expiration before doing any work.
4. Use `/api/agent` as the API base.
5. Read the discovery document's `errors`, `uploads`, `limits`, and `upload_directories` fields when
   planning file work. They are the machine-readable contract for headers, retry conditions, and
   path constraints; use this guide for the human safety policy.

Send the personal access token (PAT) on every Agent request:

`Authorization: Bearer sdm_pat_<public-id>_<secret>`

Treat the PAT and every upload task's `upload_token` as secrets:

- Keep them in a secure process environment or secret store. Never put them in a URL, source
  file, asset metadata, generated report, chat response, shell history, or log.
- Redact `Authorization`, `X-Sage-Upload-Token`, and `upload_token` values from diagnostics.
- Use HTTPS outside an explicitly authorized isolated test network.
- Stop on an expired or revoked credential. Never request broader scopes without administrator
  approval.

An upload task is bound to the human account and the exact PAT that created it. A different PAT,
even for the same account, cannot continue that task.

## 2. Authority and confirmation boundaries

A granted scope permits an API operation; it does not by itself authorize every mutation. Follow
the user's current request and any standing automation policy.

You may proceed without another confirmation for:

- catalogue searches, asset detail reads, file reads, citation exports, and upload-status checks;
- an exact metadata correction, upload, or finalization already requested by the user;
- bounded retries that follow the rules in this document and do not change the intended result.

Ask the user before proceeding when:

- more than one asset may match, or only a weak title match exists;
- required metadata is unknown, conflicting, or would have to be inferred;
- a requested PATCH could discard existing `details` fields;
- the exact target asset, archive subdirectory, or file set is ambiguous;
- formal finalization was not explicitly requested or covered by a standing policy;
- resolving a `409` would require overwriting, renaming, deleting, or choosing between records;
- cancelling a task could discard staged bytes that are not safely reproducible.

Stop instead of improvising when credentials are invalid, a required scope is absent, the task is
expired, a local file changes while hashing or uploading, a path is unsafe, or a conflict remains
unresolved. Never fabricate metadata or report success before verifying the response.

## 3. Scope matrix

| Scope | Operations |
| --- | --- |
| `assets:read` | Search/list assets and read full asset details |
| `files:read` | Preview or download indexed files |
| `metadata:write` | Create assets and update asset metadata |
| `files:upload` | Create, inspect, cancel, and upload files to isolated tasks |
| `archive:finalize` | Validate and move a staged task into the formal archive |
| `citations:export` | Export one publication as BibTeX |

`archive:finalize` is valid only together with `files:upload`: a task is bound to the exact PAT
that created it, so a different or finalize-only credential cannot complete the task.

Agent tokens cannot archive or restore assets, scan storage, claim unindexed files, change settings,
manage users, or manage access tokens. There is no `assets:archive` Agent scope.

## 4. Asset model and metadata

Asset types are:

- `paper`: work authored or produced by the lab;
- `literature`: external publications collected by the lab;
- `dataset`, `project`, and `model`: the corresponding lab assets.

Common create fields are `type`, `slug`, `title`, `summary`, `status`, `visibility`, `version`,
`tags`, and `details`.

- `slug` is globally unique, immutable after creation, 3-160 characters, and must match
  `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- `visibility` is one of `lab`, `project`, or `restricted`.
- `status` is instance-defined text, not an Agent-controlled enum. Reuse an established value when
  possible; do not invent a workflow state without user direction.
- Tags contain at most 20 non-empty values and each value is at most 80 characters.
- The authenticated human account is always the owner of an Agent-created asset. Omit
  `owner_name` and `owner_email`; they cannot be used to impersonate another owner.
- Publication `authors` contains 1-200 names; each normalized name is at most
  {{MAXIMUM_PUBLICATION_AUTHOR_CHARACTERS}} characters.
- `details` must remain compact metadata and is limited to {{MAXIMUM_ASSET_DETAILS_BYTES}} UTF-8
  JSON bytes per asset. Store large text, generated output, and binary content as files.
- Unknown `details` fields for datasets, projects, and models may be preserved, but must not be
  guessed. Use stable, descriptive keys already established by similar assets.

Every `paper` requires publication details. A `literature` record with an official `source_id`
uses the same schema. Required fields are:

```json
{
  "venue": "ACL",
  "year": 2026,
  "track": "Main Conference - Long Papers",
  "authors": ["First Author", "Second Author"],
  "source_id": "2026.acl-long.1",
  "source_url": "https://aclanthology.org/2026.acl-long.1/",
  "pdf_url": "https://aclanthology.org/2026.acl-long.1.pdf"
}
```

Optional publication fields include `publication_url`, `abstract`, `doi`, `published_at`,
`citation_key`, `entry_type`, `booktitle`, `journal`, `pages`, `publisher`, `month`, `volume`, and
`issue`. Preserve official spelling and identifiers. Store a DOI without a `https://doi.org/`
prefix when possible. Use `paper` and `literature` according to authorship, not file location.

## 5. Search and duplicate prevention

Search before every create. For a publication, search separately using each available strong
identity: DOI, official `source_id`, and title. Also inspect likely matches using the first author.
For other asset types, search by title, distinctive tags, owner, and known filenames. The search
service covers title, summary, tags, owner, filenames, and publication metadata; do not assume a
query searches `slug`.

Use:

`GET /api/agent/assets?query=<encoded-query>&asset_type=<type>&page=1&page_size=10`

Continue pagination until `page * page_size >= total`. List records are compact and omit full
`details`, summary, owner, and files. Read every plausible candidate with
`GET /api/agent/assets/{asset_id}` before deciding to create.

For `paper` and official `literature` records, the server rejects duplicates across both types when
any of these identities match:

1. normalized DOI;
2. case-insensitive official `source_id`;
3. Unicode-normalized title plus Unicode-normalized first author.

If those identities point to different existing records, stop and ask the user to resolve the
catalogue conflict. For non-publication assets, a unique slug is enforced server-side, but title
similarity still requires deliberate human review. A create `409` is not permission to generate a
different slug and create a duplicate.

## 6. Create and update workflow

Create only after completing the duplicate search:

`POST /api/agent/assets`

Before updating, read the latest asset detail. Copy its `updated_at` value exactly into the
`X-Sage-Asset-Revision` header on:

`PATCH /api/agent/assets/{asset_id}`

PATCH rules:

- Omitted top-level fields remain unchanged.
- A supplied `details` object replaces the entire existing object. Copy all fields that must remain.
- `slug`, `type`, and owner cannot be changed through this endpoint.
- A no-op PATCH is accepted and does not create an activity entry.
- On a stale-revision `409`, read the asset again, compare changes, merge deliberately, and retry
  at most once with the new revision. Never blindly overwrite concurrent work.

## 7. Reading files and citations

Asset detail responses contain indexed file IDs and relative paths. Read one with:

`GET /api/agent/files/{file_id}/content?mode=download`

Use `mode=preview` only for supported text, PDF, JSON, YAML, CSV, and image types. The endpoint
streams bytes, supports HTTP `Range`, and returns `Cache-Control: private, no-store`. Stream large
responses to disk; do not load an entire archive file into model context or memory. A file path on
the server is never exposed and cannot be supplied by the Agent.

Export a paper or literature record with:

`GET /api/agent/assets/{asset_id}/citation/bibtex`

A `409` means required citation metadata is incomplete; do not synthesize missing values.

## 8. Upload workflow

Build a local manifest before creating a task. It should contain the intended relative path, byte
size, and SHA-256 for every non-empty regular file. Do not follow symlinks or upload generated,
temporary, credential, or hidden system files unless the user explicitly selected them. If a file
changes after hashing, stop and rebuild the manifest.

1. Read the target asset again and verify its ID, title, type, and current state.
2. Choose `target_subdirectory`. Its first component must be allowed for the asset type. Additional
   components must form a canonical relative path with no empty, `.`, or `..` components and no
   repeated or trailing `/`.
3. Create a task with `POST /api/agent/uploads` and retain `upload_id`, `upload_token`, `expires_at`,
   URL fields, and `archive_relative_path` in process memory.
4. Upload each file with `PUT` to `file_upload_url_template`. Percent-encode each UTF-8 path segment
   while preserving `/` separators. Use only canonical relative paths. Empty files, empty path
   components (including repeated or trailing `/`), absolute paths, `.` or `..` components,
   components longer than 255 characters or UTF-8 bytes, reserved internal names, and paths
   longer than 1000 characters or UTF-8 bytes are rejected.
5. Send `X-Sage-Upload-Token` and, when available, the lowercase 64-character SHA-256 in
   `X-Sage-Content-SHA256`. Compare the response's `file_size` and `checksum_sha256` with the local
   manifest before continuing. A repeated PUT is idempotent only when the same path already contains
   a regular file with the same SHA-256 and the same declared length, when `Content-Length` is present.
6. The current `maximum_file_size_bytes` is {{MAXIMUM_FILE_SIZE_BYTES}}. Read the discovery
   document for this value instead of assuming the default. A `413` rejects the whole file and
   leaves no partial staged file.
7. Recover after an interruption with `GET` on `status_url` plus `include_checksums=true`,
   sending the PAT and
   `X-Sage-Upload-Token`. Every status response repeats `asset_id` and `archive_relative_path` so
   the target can be verified against the local manifest. States are `waiting`, `ready`,
   `completed`, and `cancelled`. For an active task, `files` lists every atomically accepted
   `relative_path`, `file_size`, and `checksum_sha256`; compare all three with the local manifest.
   Normal status checks may omit `include_checksums` to avoid hashing large files and then return
   `checksum_sha256` as `null`. For a completed task, `result` contains the same verified
   finalization result as `POST finalize`; for all other states it is `null`.
8. If a PUT response is lost after sending `X-Sage-Content-SHA256`, retry that exact PUT once. The
   server returns the original success response without overwriting only when the staged file matches
   the same path, declared length, and SHA-256. A `409` means the retry was not identical: inspect
   status, stop, and report the conflict. Without the original checksum, do not retry the path.
9. Before finalization, verify that every upload response succeeded or was recovered through a
   checksum-enabled status response, and that the status file list, count, and total size match the local manifest. Do not finalize an
   incomplete or ambiguous task.
10. Finalize with `POST` to `finalize_url` and JSON `{"upload_token":"<upload-token>"}`. Verify
    `imported_file_count`, `total_size`, `relative_paths`, and `checksums` in the response. Finalize
    is idempotent when repeated with the same upload ID, PAT, and upload token.
11. Cancel an unused task with `DELETE` on `cancel_url`, sending both credentials. Cancellation is
    idempotent, removes staged content, and cannot cancel a completed task.

Allowed first components:

| Type | Allowed first component |
| --- | --- |
| `paper` | `manuscript`, `supplementary`, `source`, `reviews` |
| `dataset` | `raw`, `processed`, `documentation`, `scripts` |
| `literature` | `original`, `annotations`, `notes` |
| `project` | `documentation`, `code`, `data`, `outputs` |
| `model` | `weights`, `checkpoints`, `configs`, `evaluation` |

The task's `expires_at` is authoritative. Do not start or retry work after it. Never overwrite or
silently rename a path. Finalization rejects existing archive paths, duplicate content within the
task, and content already indexed elsewhere. Ask the user how to resolve those conflicts.

## 9. Retry and error policy

Agent authentication, request-validation, and domain errors use `{"detail":"message"}` and return a stable
`X-Sage-Error-Code` header. Read `errors.codes` from discovery and branch on the code, not
localized `detail` text; preserve the HTTP status, code, and sanitized detail in diagnostics.
Every Agent request-validation response (`422`) uses `request_invalid`, including errors raised
before route logic.

| Agent error code | Required action |
| --- | --- |
| `agent_auth_required` | Supply the PAT through the Bearer authorization header. |
| `agent_auth_invalid` | Stop; the PAT is invalid, expired, revoked, or belongs to a disabled account. |
| `agent_auth_unavailable` | Stop and report that the server credential configuration is unavailable. |
| `agent_scope_missing` | Stop; report the required scope without broadening the credential automatically. |
| `asset_not_found`, `file_not_found` | Re-read or search; the record may be absent or archived. |
| `asset_slug_conflict`, `asset_metadata_conflict` | Search and compare identities; do not create a renamed duplicate or overwrite metadata. |
| `asset_revision_conflict` | Read the latest asset, merge deliberately, and retry at most once. |
| `file_preview_unavailable` | Retry in download mode only when reading the full file is appropriate. |
| `file_unavailable` | Stop and report that the indexed file needs server-side storage reconciliation. |
| `citation_incomplete` | Stop; do not synthesize the missing citation fields. |
| `request_invalid` | Correct missing headers, fields, types, or parameter bounds using OpenAPI before retrying. |
| `range_invalid` | Rebuild the Range header from the byte length returned by HEAD before retrying. |
| `range_not_satisfiable` | Re-run HEAD and reconcile the remote length with the local partial file before resuming. |

Upload operations add the upload-specific codes below. `upload_busy` also includes
`Retry-After`; wait at least that many seconds, inspect task status, and retry only when the
operation table below permits it.

| Upload error code | Required action |
| --- | --- |
| `upload_token_missing` | Supply the original task token in the required header. |
| `upload_credentials_invalid` | Stop; the task, PAT, or task token is invalid, expired, or mismatched. |
| `upload_target_invalid` | Re-check the asset and allowed archive directory; ask if intent is ambiguous. |
| `invalid_content_length`, `invalid_checksum` | Correct the malformed request header before retrying. |
| `upload_too_large` | Stop; split or otherwise change the payload only with user authorization. |
| `upload_busy` | Honor `Retry-After`, inspect status, and make one safe retry. |
| `upload_not_ready` | Inspect status and reconcile the accepted files with the local manifest. |
| `upload_conflict` | Stop and resolve the path or duplicate-content conflict with the user. |
| `upload_invalid`, `upload_status_unavailable`, `upload_cancel_failed` | Stop and diagnose the reported storage, path, checksum, or task-state failure. |

| Status | Required action |
| --- | --- |
| `400` | Correct the malformed header or request before retrying. |
| `401` | Stop; obtain valid PAT or required upload credentials. |
| `403` | Stop; verify scope and task/PAT ownership. Do not broaden scope automatically. |
| `404` | Re-read/search; the resource may be absent or archived. |
| `409` | Inspect `X-Sage-Error-Code` when present, then use the detail and current state to resolve the conflict before retrying. |
| `413` | Do not retry the same payload; the file exceeds the limit. |
| `416` | Re-run HEAD and verify whether the local file is complete, stale, or must be restarted. |
| `422` | Correct fields and types using OpenAPI before retrying. |
| `429` | Honor `Retry-After`, otherwise use the bounded schedule below. |
| `5xx` | Retry only when the operation is safe according to the table below. |

For transient read failures, use at most three retries after approximately 1, 2, and 4 seconds,
with jitter. Honor a longer `Retry-After`. Do not run unbounded polling or retry loops.

| Operation | Ambiguous timeout or `5xx` handling |
| --- | --- |
| GET/search/detail/status | Safe to retry with bounded backoff. |
| File download | Resume with `Range` only after validating response headers; otherwise restart to a temporary file. |
| POST asset create | Do not blindly repeat. Search again by all identities and slug first. |
| PATCH asset | Read latest detail and retry only if the intended merge is still valid. |
| POST upload task | Do not blindly repeat because the first task may exist but its secret response was lost. Search cannot recover that token; report the ambiguity and allow it to expire. |
| PUT file | With the original SHA-256, retry the exact PUT once; identical accepted content returns `200`. On `409`, inspect status and stop. Without the checksum, do not retry the path. |
| POST finalize | Safe to retry with the same upload ID, PAT, and upload token. |
| DELETE cancel | Safe to retry with the same upload ID, PAT, and upload token unless status is `completed`. |

## 10. End-to-end examples

Assume `BASE_URL` and `SAGE_TOKEN` are already supplied securely in the process environment. The
examples also use `jq`, `sha256sum`, and a local non-secret file path.

Verify identity and scopes:

```sh
curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/me" \
  -H "Authorization: Bearer $SAGE_TOKEN"
```

Search without hand-encoding the query:

```sh
curl --fail-with-body --silent --show-error --get \
  "$BASE_URL/api/agent/assets" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  --data-urlencode 'query=10.18653/v1/2026.acl-long.1' \
  --data-urlencode 'asset_type=literature' \
  --data-urlencode 'page=1' \
  --data-urlencode 'page_size=10'
```

Create a project only after duplicate review:

```sh
curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/assets" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "type":"project",
    "slug":"multimodal-understanding",
    "title":"Multimodal Understanding",
    "summary":"Lab project for scientific chart understanding.",
    "status":"active",
    "visibility":"lab",
    "tags":["multimodal","scientific-charts"],
    "details":{"started_at":"2026-08-16"}
  }'
```

Read and update without discarding `details`:

```sh
DETAIL_JSON="$(curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/assets/$ASSET_ID" \
  -H "Authorization: Bearer $SAGE_TOKEN")"
REVISION="$(printf '%s' "$DETAIL_JSON" | jq -r '.updated_at')"

printf '%s' "$DETAIL_JSON" | jq \
  '{summary:"Verified summary.", details:.details}' | \
curl --fail-with-body --silent --show-error \
  -X PATCH "$BASE_URL/api/agent/assets/$ASSET_ID" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H "X-Sage-Asset-Revision: $REVISION" \
  -H 'Content-Type: application/json' \
  --data-binary @-
```

Create, upload, inspect, and finalize one file:

```sh
TASK_JSON="$(jq -n --arg asset_id "$ASSET_ID" \
  '{asset_id:$asset_id,target_subdirectory:"original"}' | \
curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/uploads" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @-)"

UPLOAD_ID="$(printf '%s' "$TASK_JSON" | jq -r '.upload_id')"
UPLOAD_TOKEN="$(printf '%s' "$TASK_JSON" | jq -r '.upload_token')"
CHECKSUM="$(sha256sum "$FILE_PATH" | cut -d ' ' -f 1)"

curl --fail-with-body --silent --show-error \
  -X PUT "$BASE_URL/api/agent/uploads/$UPLOAD_ID/files/paper.pdf" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H "X-Sage-Upload-Token: $UPLOAD_TOKEN" \
  -H "X-Sage-Content-SHA256: $CHECKSUM" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary "@$FILE_PATH"

curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/uploads/$UPLOAD_ID" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H "X-Sage-Upload-Token: $UPLOAD_TOKEN"

jq -n --arg upload_token "$UPLOAD_TOKEN" '{upload_token:$upload_token}' | \
curl --fail-with-body --silent --show-error \
  "$BASE_URL/api/agent/uploads/$UPLOAD_ID/finalize" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @-
```

Cancel instead of finalizing when the task is no longer needed:

```sh
curl --fail-with-body --silent --show-error \
  -X DELETE "$BASE_URL/api/agent/uploads/$UPLOAD_ID" \
  -H "Authorization: Bearer $SAGE_TOKEN" \
  -H "X-Sage-Upload-Token: $UPLOAD_TOKEN"
```

All Agent mutations and file reads are attributed to the human account and PAT name in the
activity log. Report the verified asset ID, final relative paths, sizes, and checksums after a
successful operation, but never include either secret token.
