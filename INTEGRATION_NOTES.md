# Integration notes

## Stack override
The owner explicitly selected FastAPI + PostgreSQL instead of Node.js/Fastify/NestJS.
API casing, endpoints, enums and semantics remain governed by contract v1.0.0.
Pydantic will validate Python API boundaries. Shared frontend Zod schemas remain a future integration deliverable.

## Known contract discrepancy
Section 12.3 step 7 must explicitly send oldDirectionRevisionId = 00000000-0000-4000-8000-000000000031 after step 5 activates Direction B. Otherwise section 6.8 defaults old direction to B and rejects the identical old/new revisions.

## Implemented scope
Initial foundation has been extended by step 2 below. Mock assessment results are not implemented yet.
Internal /ready is a deployment probe outside the versioned public contract.

## Verification
python -m pytest -q
docker compose config --quiet
Contract SHA-256: EA050C8AA413FC56E60E6676336754132624F4CD6A0EB776930718A1CC3150B2
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


Verification results: 13 isolated API/unit tests passed; live PostgreSQL smoke passed (4 concurrent brief writes, direction history preservation and idempotent replay). Docker rebuilt successfully; /ready returned ok.

Owner override: backend default port is 8000; frontend API base URL must be http://localhost:8000/api/v1 (contract examples use 3001).

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


## Final demo and integration endpoints (step 7)
The five pending features are now implemented. Contract 1.0.0 remains unchanged. GET project summary uses unresolved/deferred items from the latest COMPLETED assessment, even if a newer assessment failed. Stale count uses the latest non-superseded revision of each non-archived collection. Recent decisions are newest five across all supported decision sources.

Full Solara IDs/outcomes follow contract sections 9.1 and 9.3. Placeholder JPEGs are synthetic, local and labeled. Approved collection pins never advance automatically. DEMO_SOLARA clones use fresh IDs. Startup extends an untouched workspace with the full seed and preserves existing assets; reset is an explicit boundary on permanent project ...0010, preserving other projects and uploads. Matching idempotency records are removed to prevent stale replays after reset. Actor-before-project locks match mutation lock order. Reset reuses project identity to preserve storage foreign keys.

AI configuration remains env-only. Public settings never return keys. The mock connectivity test performs no network request. An explicit real test uses AI_BASE_URL/chat/completions and the configured model/key, with a 15-second timeout and 8-token response cap. Safe errors use AI_NOT_CONFIGURED, PROVIDER_TIMEOUT or PROVIDER_ERROR. openai is retained as the contract protocol enum; it does not restrict model ownership. Gemini through an OpenAI-compatible router is supported for connectivity. Native Gemini protocol and real creative-analysis calls are future adapters.

Exports are inline text per contract and persist under migration 006. JSON contains complete project histories and assessment items; Markdown adds readable sections; CSV is one row per asset/latest version with formula-injection protection. Recursive filtering removes configured credentials, secret/storage-key fields, URL user-info and query parameters including signed download URLs. No media bundling; warnings describe portability and metadata-only versions. Every export logs EXPORT_CREATED in the same transaction. Reset logs DEMO_RESET.

The live end-to-end test explicitly includes oldDirectionRevisionId when assessing after activating the target direction: omitting it would default to the already-active target and correctly fail old=new validation. This resolves the ambiguity in contract smoke step 7 without altering the wire contract.
Verification completed: 74 API/unit tests passed. Docker Compose rebuilt successfully; API :8000 and PostgreSQL :5436 are healthy. Live system smoke passed using an isolated DEMO_SOLARA clone. PostgreSQL reset and foreign-key cleanup verified inside a transaction, then rolled back to preserve existing demo data. Real router requests were mocked; no billable provider call was made.

## Router adapter integration (step 8)
Supersedes earlier notes that real provider adapters were pending. Added a reusable OpenAI-compatible router transport using AI_BASE_URL, AI_API_KEY, AI_MODEL and AI_PROVIDER_NAME. The wire mode remains openai under contract 1.0.0; any compatible router model may be selected. Native incompatible protocols require another adapter. AI_TIMEOUT_SECONDS, AI_MAX_OUTPUT_TOKENS, AI_JSON_MODE and AI_IMAGE_ANALYSIS_ENABLED are forwarded by Compose.

AI_ASSISTED accepts validated AI classification; HYBRID preserves deterministic rule recommendations/priority and adds validated AI explanations/evidence/actions. Extra warnings continue to require human review. All returned IDs/evidence must belong to input scope; image evidence must name the exact version whose bytes were sent. Metadata records actual provider/model, branchframe-router-v1 prompt template, simulation flag and image count. Rules/mock modes never call the router. Real errors persist FAILED with safe provider error codes; retry still uses original snapshots.

Vision uses only uploaded/demo local images, downscaled to at most 1024x1024, sent as base64 JPEG. Assessment snapshots retain those image bytes for consistent retries; public URLs are never fetched. Reference output validates all attributes before writing AI_INFERRED/UNREVIEWED rows. Configuration declares vision availability; it cannot guarantee the selected model accepts images. Image observations are now accepted only when matching supplied input images, replacing the earlier global zero-image restriction.

No automatic retry or fallback to mock. Cost accounting/monthly budget enforcement remains unimplemented; output-token limits do not cap total input/reasoning/provider costs. Processing remains synchronous. Tests clear all real environment credentials. A local HTTP router can exercise the entire adapter/PostgreSQL path without paid calls (scripts/smoke_router_local.py).
Verification: 97 API/unit tests passed with real .env credentials isolated. A real HTTP transport smoke passed inside Docker against a local fake router and PostgreSQL, covering image assessment, reference attributes and the connectivity endpoint. AI_MODE=openai was activated only after the existing base URL/key/model configuration passed local presence/URL checks. Actual credential validity has not been tested against a paid provider. Vision remains controlled by the user's existing flag. Retry respects current scope/vision controls before sending a request.
