# BranchFrame backend

## Current implementation status

The five final backend features are implemented: full Solara demo seed, project summary, env-only AI settings/connectivity test, JSON/Markdown/CSV exports, and scoped demo reset. Earlier step notes below describe historical progress.

New endpoints:
- GET /api/v1/projects/:projectId/summary: counts, active direction, latest assessment, unresolved recommendations, stale pins and five recent decisions.
- GET /api/v1/settings/ai: public configuration only, no credentials.
- POST /api/v1/settings/ai/test: simulated success in mock mode; one explicit small chat-completion request in openai-compatible router mode. Real assessment/reference adapters are implemented; setup is described in AI_SETUP.md.
- POST /api/v1/projects/:projectId/exports with format JSON, MARKDOWN or CSV: inline downloadable content, warnings, persisted export record and audit event. JSON includes complete histories; Markdown has readable sections plus a full structured appendix; CSV contains each asset's latest version. Uploaded media is not bundled. Credential fields, configured keys and signed URLs are excluded; CSV guards formula injection.
- POST /api/v1/demo/reset with confirm: true (strict boolean): restores only the permanent demo project ...0010. Other projects and uploaded files remain. Disabled when DEMO_MODE=false. The demo project's versions, decisions and collections are replaced with the baseline; this is an explicit destructive demo action.

Migration 006 adds export records. New workspaces and DEMO_SOLARA clones have four assets, five versions, two references and one approved collection. Seven locally generated synthetic JPEG placeholders are served at /demo-assets/*. Startup preserves existing demo versions and user changes. To obtain a clean baseline for an already customized demo, explicitly use the reset endpoint.

Router configuration is in .env: AI_BASE_URL (the API base including /v1 when required), AI_API_KEY, AI_MODEL, AI_PROVIDER_NAME. Use AI_MODE=openai for real router assessments; mock remains available without network calls. AI_MODE=openai is the contract's legacy compatible-protocol mode; the model can be Gemini or another model exposed through your router. In this mode assessments use the router; reference image analysis additionally requires AI_IMAGE_ANALYSIS_ENABLED=true and a vision-capable model. Native provider protocols require a separate adapter. Calls occur only when the user explicitly requests the test; no real call was made during implementation.

Run from this backend directory: docker compose up -d --build. API: http://localhost:8000; API docs: http://localhost:8000/docs.

Verification: python -m pytest -q -p no:cacheprovider; python scripts/smoke_system_postgres.py. The live smoke uses a fresh demo clone and archives it afterward. scripts/verify_reset_transaction.py verifies PostgreSQL reset constraints and rolls back all changes; run it in the API container with its configured database URL.

Remaining scope: session authentication and role enforcement, shared frontend Zod schemas, measured budget enforcement, and an asynchronous analysis worker. This remains a local/demo backend.

FastAPI + PostgreSQL + SQLAlchemy + Pydantic. Docker configuration is contained in this backend repository.

## Run with Docker
From this folder:
```powershell
docker compose up -d --build
docker compose ps
```

API: http://localhost:8000/api/v1/health
OpenAPI: http://localhost:8000/docs
Readiness (database check): http://localhost:8000/ready
PostgreSQL: localhost:5436. Data and uploads persist in named Docker volumes.

Optional: copy .env.example to .env to customize development configuration.
Default credentials are for local development. Services bind to loopback.
Stop with docker compose down (keeps volumes).

## Local development
Python 3.12:
```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
.venv/Scripts/python.exe -m pytest -q
```
Run the database with docker compose up -d db first.

## Scope
API foundation plus project, brief-revision and direction modules are implemented. See the step 2 section below for exact scope and remaining modules. AI_MODE records configuration only; no provider calls occur.

Wire contract: ../agents/INTEGRATION_CONTRACT.md v1.0.0.
## Workspace implementation (step 2)

Implemented projects (list/create/read/edit/archive/restore), immutable brief revisions, direction creation/cloning/revisions, deterministic direction diff, and active-direction changes. Initial versioned migration runs at startup; mutation activity and change-direction decisions are persisted in the same transaction. Project creation supports idempotency. Revision numbering is allocated under a PostgreSQL project-row lock.

Seed: fixed demo user/project/brief and two directions from contract section 9. Assets, references, approved collection, assessments and demo reset are not implemented yet; DEMO_SOLARA currently creates only this workspace subset. No response claims an assessment or approval exists.

Pydantic response schemas for projects/briefs/directions appear in OpenAPI. No shared frontend Zod package is shipped yet.

Unit/API tests use an isolated in-memory SQLite database; they do not clear the development database.
Run live PostgreSQL checks against the running Compose API:
```powershell
python scripts/smoke_postgres.py
```
This creates dedicated smoke projects and archives them after verification; it leaves the permanent Solara seed unchanged.


## Asset implementation (step 3)
Implemented asset registration/list/search/filter/edit, version creation/history/detail/lineage, audited version-status changes, and version-specific approve/reject/revision decisions. Migration 002 adds assets, versions and version decisions. Concurrent version writes lock the project row; idempotent asset/version creation replays the original response.

Supported file inputs currently: external HTTP(S) URLs (not fetched by the server) or explicit metadata-only versions. Storage-object and reference IDs are validated as unavailable until their modules are implemented. Collections are not implemented, so pinnedIn is currently empty; no pin-preservation claim is made yet.

Decisions for asset versions are stored in asset_version_decisions, separate from existing direction-change decisions. Their wire DTO follows section 5.8. A future project decision-history endpoint must combine these sources.
Asset status is marked APPROVED on version approval; creation of a newer version preserves this asset status and the older approval, as required by section 6.6.
Direction/brief context defaults only for omitted fields; explicit null means no association.

Run live asset checks: python scripts/smoke_assets_postgres.py. This uses a new temporary project and archives it after checking; permanent seed data is untouched.


## Collections implementation (step 4)
Implemented all section 6.10 collection endpoints: create/list/detail/edit/archive, revisions, exact-version pins, reorder/remove, review submission, approve/reject.
Migration 003 adds collections, revisions, items, approvals and pin decisions. New asset versions preserve collection pins. Version detail now returns actual pinnedIn entries. Non-DRAFT item edits fail with REVISION_FROZEN; duplicate asset pins fail with ASSET_ALREADY_PINNED; only one DRAFT/IN_REVIEW revision is allowed.
New revisions copy exact old pins. Only explicit same-asset pin replacement with a rationale moves a pin. Approving a new revision supersedes the earlier approved revision without changing its pins or approvals.
Positions are rearranged in two transactional phases to avoid transient PostgreSQL uniqueness conflicts; committed positions remain positive and gapless.
Pin decisions are stored in collection_pin_decisions; future unified decision-history/export endpoints must include this table alongside direction and version decisions.
Collection metadata/item actions without a dedicated contract activity enum use PROJECT_UPDATED with their actual entityType. Collections keep APPROVED while a subsequent draft/review is open; archived status is preserved during review actions. Restore derives status from approvals/latest review.
Full fixed Solara asset/reference/collection seed and demo reset are still pending.
Verification commands: python -m pytest -q; python scripts/smoke_collections_postgres.py.


## Reference library and upload implementation (step 5)
Migration 004 adds storage objects, references, attributes and reference links. Section 6.3 upload and section 6.7 reference endpoints are implemented. Asset versions now accept uploaded files and references, use validated MIME/dimensions/checksum, and expose resolved file URLs. Asset detail includes actual asset-reference links.

Uploads: actual PNG/JPEG/WebP signatures, maximum 10 MiB and 8000 pixels per side. Multipart body is bounded to 11 MiB before parsing (includes overhead). Generated storage names stay under STORAGE_LOCAL_DIR. A database failure removes the newly written file.
Storage download is an internal GET /api/v1/files/:id endpoint with a 15-minute HMAC URL. API_PUBLIC_BASE_URL defaults to http://localhost:8000. Signing key is generated per API process: refetch URLs after restarts; use a single worker for this local MVP. Uploaded files persist in the Docker uploads volume. Uvicorn access logging is disabled in Docker to avoid logging signed query strings.
Public reference URLs are registered and never fetched. Mock analysis requires an uploaded image to exist and returns AI_INFERRED/UNREVIEWED attributes with meta.simulated=true. Attribute correction preserves origin. Real provider analysis is not implemented; it returns AI_NOT_CONFIGURED. No automatic billable calls occur.
Reference links support directions, assets and exact asset versions, enforce project scope, and are idempotent on their documented tuple. Version-creation reference snapshots are stored with immutable version metadata.
Full demo image assets/seed, impact assessments, activity/decision list APIs, exports and reset are still pending.
Checks: python -m pytest -q; python scripts/smoke_references_postgres.py.


## Impact assessments and decision history (step 6)
Migration 005 adds assessments, assessment items and append-only assessment decisions. Section 6.8 endpoints are implemented, including polling reads, retry on the same failed ID, cancellation state checks, and ACCEPT/OVERRIDE/DEFER/DISMISS.
Rules-only and deterministic mock AI run inline and return COMPLETED (or FAILED). There is no asynchronous worker or real AI adapter yet. AI modes require AI_MODE=mock; openai mode returns AI_NOT_CONFIGURED. includeImages is not used by these providers; imagesSent is always 0 and IMAGE_OBSERVATION evidence is rejected.
Rules classify recorded metadata only: 2+ missing style categories -> review/high uncertainty; hard forbidden conflicts or 3+ style-category conflicts -> recreate; 1-2 conflicts plus recorded composition -> adapt; 2+ overlaps with no conflicts -> reuse. Unproven hard requirements -> review. Token matching does not infer visual quality or rights.
Each run snapshots input version IDs and their recorded attributes, target direction, brief and pinned-in-approved status. Retry uses this snapshot, even if new versions are created later. Output validation enforces one item per input version, valid enums and evidence source IDs. Invalid/failed output persists FAILED with safe error text and no items.
User decisions are append-only, link superseded decisions, preserve the immutable recommendation, and never modify assets, versions or collection pins.
GET /projects/:id/decisions combines direction, version, pin and assessment decisions. GET /projects/:id/activity exposes persisted audit events with filters and cursor pagination. Asset detail includes assessment decisions.
The permanent demo seed still contains only workspace entities; the full section 9 seed is pending. Project summary, exports, settings endpoints and demo reset are also pending. Shared frontend Zod schemas and real-provider integration remain future work.
Verification: 55 API/unit tests passed, including fault injection and retry-scope tests. Live check: python scripts/smoke_assessments_postgres.py.


## Generic AI router configuration

Set AI_BASE_URL, AI_API_KEY, AI_MODEL, and AI_PROVIDER_NAME in backend/.env. The model identifier follows your router's catalog, so it can point to Gemini or another supported provider. Docker Compose forwards these variables to the API container. Existing values in .env are preserved.

AI_MODE=mock is the example default. AI_MODE=openai now enables the implemented compatible router adapter. See AI_SETUP.md for configuration and troubleshooting. The contract's legacy openai mode denotes the intended compatible API protocol, rather than requiring an OpenAI model. Never commit .env or API keys.
## Real router integration (step 8)
Assessment and reference analysis now call the configured chat-completions router in openai protocol mode. AI_ASSISTED classifies with AI; HYBRID keeps rules classifications and enriches evidence/reasoning with AI. Responses are validated for exact version scope and traceable evidence. IMAGE_OBSERVATION requires a matching local image actually included in the input. Failed output never creates partial items. Retries preserve the original input snapshot. Provider/model/prompt-version/simulation/image-count metadata reflects the selected execution mode. Timeout failures persist PROVIDER_TIMEOUT safely.

Reference image attributes are validated as a batch before writes and remain AI_INFERRED/UNREVIEWED. Only local uploads/demo images are sent as resized base64 JPEGs; no arbitrary URL fetching. Vision is explicitly enabled by AI_IMAGE_ANALYSIS_ENABLED. The configured token cap and timeout apply to each request; no automatic retries or silent mock fallback. Credentials are never included in prompts or returned in error details. Tests explicitly isolate .env credentials.

See AI_SETUP.md for activation, a sample router URL, and the explicit connectivity command. Tests use simulated HTTP responses or a local fake router; actual paid-provider credentials were not tested.