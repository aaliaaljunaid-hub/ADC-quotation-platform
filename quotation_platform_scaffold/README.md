# Designer Quotation Platform — Catalog-Driven Scaffold

Rebuilt around the real pricing model traced out of `ADC-BOQ MASTER -2026-v6.xlsx`
(catalog item -> quantity x unit price -> grouped by category in a fixed
construction sequence), not the earlier generic tiered-pricing model.

## What's here
- `app/db_models.py` — SQLAlchemy schema: accounts, users, categories, catalog_items,
  projects, project_floors, rooms, doc_templates, terms_templates, quotes, quote_line_items.
- `app/seed_data.py` — the 35 default categories (code / name / two-letter tag prefix /
  print sequence), traced from the workbook's `Tag Codes` + `Lists` sheets.
- `app/models.py` — Pydantic request/response schemas for the API.
- `app/pricing_engine.py` — line_total = qty x unit price; groups line items by category
  in `process_sequence` order; computes subtotal/VAT/grand total/payment schedule.
- `app/docx_generator.py` / `app/xlsx_generator.py` — branded .docx and .xlsx generators,
  same layout mirroring the ADC/Consilio sheets: category header rows, per-category Sr.No,
  Total/VAT/Grand Total, payment schedule, terms & conditions, signature block. Logo +
  charcoal/gold (or custom) colors come from the account's `DocTemplate`; a `show_tag_column`
  flag reproduces the `-Tag` variants. Both read the exact same quote data, so the two files
  never disagree with each other.
- `app/security.py` — PBKDF2 hashing for both access codes and user passwords (no plaintext
  secrets at rest); `hash_password`/`verify_password` are aliases of the same functions.
- `app/auth.py` — session auth: `/auth/redeem` (access code) or `/auth/login` (per-user
  email/password) both issue the same signed JWT (subject = account_id, not user_id — a
  session is account-wide either way) carrying a `role` claim (access-code sessions are
  always `"owner"`; a per-user login carries that user's own role); every account/project/
  quote-scoped endpoint requires the token and checks the caller actually owns the resource
  being addressed, via `Depends(get_current_account)` + `require_account_match` (path has
  `account_id`) or the `get_owned_project`/`get_owned_quote` helpers (path only has a resource
  id, so ownership is checked through its FK chain). `require_owner` additionally checks the
  role claim — used for team management (`POST /accounts/{id}/users`) and starting a billing
  checkout; everything else (catalog, projects, quotes, branding) is open to any team member.
  The access code stays the way to bootstrap a brand-new account; named team members with
  their own email/password are added after that via the owner-only endpoint above.
- `app/billing.py` — Stripe subscription gating: `require_active_subscription` blocks the
  value-producing quote actions (create/add-line-item/update-line-item/calculate/generate-
  document) once `subscription_status` isn't `trial`/`active` — catalog and branding
  management stay available even when lapsed. A `/billing/webhook` endpoint is the source
  of truth for that status (checkout completion, renewals, payment failures, cancellations)
  since Stripe, not our own redirect handler, is what actually knows what happened.
- `app/cost_buildup.py` + `app/db_models.py`'s `RateLibraryEntry`/`CostBuildupDefaults`/
  `FitOutBenchmark` — v2 estimation aids traced from the source workbook's `Rate Library`,
  `Rate Build-Up` and `Fit-Out Benchmarks` sheets: a frozen historical-rates reference table,
  a wastage/overhead/margin calculator that suggests a `client_unit_price` for an unpriced
  catalog item, and indicative BHD/m² bands by project type. None of these write to a
  catalog item automatically or feed quote pricing — they're reference tools a designer
  consults, matching how the original sheets worked. Also, unlike the quote-building
  endpoints, none of these are subscription-gated (they're not the paid, value-producing
  path — a lapsed account can still consult its reference data).
- `app/main.py` — FastAPI app wiring it all together.
- `app/database.py` — SQLAlchemy session/engine. Defaults to a local SQLite file
  (`quotation.db`) so this runs with zero setup; set `DATABASE_URL` to a Postgres DSN
  for production (`pip install psycopg2-binary` first).
- `migrations/` — Alembic migrations, the production alternative to the dev-only
  `create_all` in `main.py`'s startup handler (see "Migrations & production DB" below).
- `app/storage.py` — asset storage abstraction for uploaded logos: local disk (default) or
  S3-compatible object storage (set `STORAGE_BACKEND=s3`; see "Asset storage" below).

All money fields are BHD, stored as `numeric(12,3)` and printed `BHD #,##0.000`.

## Run it
```
pip install -r requirements.txt
export JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")  # required outside dev
uvicorn app.main:app --reload
```
Without `JWT_SECRET_KEY` set, the app logs a warning and falls back to an insecure
dev default — fine for local testing, never for anything reachable by anyone else
(anyone with that fixed string can forge a session for any account).

Then visit http://127.0.0.1:8000/docs for interactive API docs (Swagger UI). Every
endpoint except `POST /accounts`, `POST /auth/redeem` and `GET /health` requires an
`Authorization: Bearer <token>` header — click "Authorize" in Swagger UI and paste
the `access_token` from `/auth/redeem`'s response.

Typical flow to try end-to-end from `/docs`:
1. `POST /accounts` — creates the account and a default (charcoal/gold) `DocTemplate`.
2. `POST /auth/redeem` — exchange the access code for a session token; use it as a Bearer
   token on everything below. `GET /auth/me` resolves the account from the token alone
   (this is what the frontend calls on page load to restore a session).
3. `GET /accounts/{account_id}/categories` — see the 35 seeded categories.
4. `POST /accounts/{account_id}/catalog-items` — add a few catalog items against those categories
   (or skip 4-6 entirely and use `POST /accounts/{account_id}/import/workbook` — see below).
5. `POST /accounts/{account_id}/projects` — create a project.
6. `POST /accounts/{account_id}/quotes` — create a quote for that project.
7. `POST /quotes/{quote_id}/line-items` — add line items (pass `catalog_item_id` to pull
   description/unit/tag/price from the catalog, or fill in a custom line directly).
8. `GET /quotes/{quote_id}/calculate` — live price preview, grouped by category — call this
   on every line-item change from the React frontend.
9. `POST /quotes/{quote_id}/generate-document` — downloads the branded .docx.
10. `POST /quotes/{quote_id}/generate-spreadsheet` — downloads the same quote as a branded
    .xlsx instead (same category grouping, totals, payment schedule).

## Importing a real ADC-BOQ workbook
`POST /accounts/{account_id}/import/workbook` (multipart file upload) reads a real
`ADC-BOQ MASTER`-style `.xlsx` and, in one shot, creates a Project, a Quote, and
CatalogItems reproducing exactly what the `ADC` sheet showed:
- `3.Selected` is already a flat, one-row-per-selected-item list (category, sub-category,
  tag, description, quantity, unit, client unit cost, supplier unit cost) — no header-row
  logic needed, that only exists in the *rendered* `ADC`/`Consilio` sheets.
- The `ADC` sheet's header cells (`B9`=client, `B10`=project name, `C10`=quote number,
  `E9`=project number) become the Project/Quote fields, so the imported quote number matches
  the original (e.g. `ADC-2025-502-1`) instead of being freshly generated.
- Categories are matched by name (stripping the "NN." prefix, e.g. "01.Design" → "Design")
  against the 35 seeded categories; an unrecognized category gets created on the account.
- CatalogItems are upserted by tag within the account — re-importing the same workbook (or a
  revised version of it) updates existing items instead of duplicating them, and reuses the
  existing Project if its project_number already exists for that account.

Verified against the actual source workbook end to end: the imported quote's subtotal/VAT/
grand total matched the values traced by hand from the `ADC` sheet exactly (506.000 /
50.600 / 556.600), and re-importing correctly updated rather than duplicated the 4 catalog
items. See `app/xlsx_importer.py` for the exact column mapping.

## Converting reference documents to Markdown
`POST /accounts/{account_id}/import/document-markdown` (multipart file upload) runs a
supplier quote, client brief or third-party BOQ (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`,
`.txt`, `.md`, `.html`, `.json`, `.xml`; 20 MB max) through Microsoft's
[markitdown](https://github.com/microsoft/markitdown) and returns
`{filename, title, markdown, character_count}` — headings, lists and tables preserved, so
spreadsheet sheets come back as Markdown tables. It's a read-only reference tool: nothing is
saved and nothing touches the catalog or a quote (use the workbook import above for that), so
it's open to any team member and not subscription-gated. markitdown plugins and LLM image
captioning are disabled, so conversion runs locally and the upload never leaves the server.
See `app/document_converter.py`.

Estimation aids, independent of the flow above:
- `GET/POST/DELETE /accounts/{account_id}/rate-library` — reference rates (`?search=` filters
  by description); nothing here links live to a catalog item, it's copy-from-and-adjust.
- `GET/PATCH /accounts/{account_id}/cost-buildup/defaults` — per-account wastage/overhead/margin
  assumptions (defaults 5% / 10% / 15%, get-or-created on first read).
- `POST /accounts/{account_id}/cost-buildup/calculate` — given material + labour cost per unit
  (and optional per-call overrides for wastage/overhead/margin), returns the full build-up
  chain (wastage, prime cost, overhead, total cost, margin, proposed unit rate, proposed total)
  so a designer can decide whether to accept it as a catalog item's `client_unit_price`.
- `GET /accounts/{account_id}/fitout-benchmarks?project_type=Office` — indicative BHD/m² low/high
  bands by trade element, seeded globally from the source sheet (Office/Retail/Restaurant x
  MEP/Partitions/Flooring/Ceiling/Joinery & finishes).

## Client-facing quote sharing
Every quote gets a `share_token` (an unguessable `secrets.token_urlsafe(24)`, not a JWT — the
client has no account) at creation. `GET /public/quotes/{share_token}` and
`POST /public/quotes/{share_token}/accept` need no auth at all; the token itself is the only
credential, and the response never includes `supplier_unit_cost` or anything else internal.
Accepting records a typed name + timestamp (a lightweight "signature", not a real e-signature
service) and flips `status` to `accepted`, at which point `add_line_item`/`update_line_item`/
`delete_line_item` all start returning 409 — an accepted quote is immutable from then on.
The frontend's Quote Builder shows the link with a copy button once a quote exists;
`frontend/src/pages/PublicQuote.tsx` is the client-facing page itself, reachable with zero
login at `/quote/:shareToken`.

## Frontend
`frontend/` is a Vite + React + TypeScript SPA against this API: access-code or per-user
email/password login, a Team page for adding named users, a workbook-import button on
Projects, catalog manager, project/room editor, a quote builder with the live price preview
(calls `/quotes/{id}/calculate` on every line-item change), an Estimation Aids page (cost
build-up calculator with a "push to catalog" action, Rate Library, Fit-Out Benchmarks),
branding settings (colors, logo upload, payment schedule), one-click `.docx` or `.xlsx`
download, a copyable client link once a quote exists, and the public `/quote/:shareToken`
page itself (no login, client-side route mounted outside the authenticated app shell).
```
cd frontend
npm install
npm run dev        # http://localhost:5173, expects the API on http://localhost:8000
```
Set `VITE_API_BASE_URL` if the API isn't on the default `http://localhost:8000`, and
`CORS_ORIGINS` on the backend if the frontend isn't on the default `http://localhost:5173`.

## Billing (Stripe)
Set these to actually charge anyone (without them, every account behaves as if it were
always on `trial` for gating purposes, but `POST .../billing/checkout-session` fails
with a clear 500 rather than a confusing crash):
- `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID` — for `POST /accounts/{account_id}/billing/checkout-session`,
  which creates a subscription Checkout Session and returns its URL to redirect the user to.
- `STRIPE_WEBHOOK_SECRET` — for `POST /billing/webhook`. Point a Stripe webhook endpoint at
  this URL subscribed to `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`, and `invoice.payment_failed`.

I verified the gating logic and the webhook's signature verification + status-mapping
end to end using synthetic signed payloads (Stripe's signing scheme is pure HMAC, so this
doesn't need network access) — but **I have not tested `create_checkout_session` against
a real Stripe account**, since this environment has no Stripe API key. Get a test-mode
secret key from the Stripe dashboard and do one real checkout run before shipping this.

## Migrations & production DB
Dev default: `main.py`'s startup handler calls `Base.metadata.create_all()` against the
SQLite file, so nothing needs setting up to run this locally. For Postgres, use Alembic
instead:
```
pip install psycopg2-binary
export DATABASE_URL=postgresql://user:pass@host:5432/dbname
export AUTO_CREATE_TABLES=false   # stop main.py's create_all from also running
alembic upgrade head
```
`migrations/env.py` reads `DATABASE_URL` the same way `app/database.py` does, so migrations
always target whatever DB the app itself would connect to — nothing is duplicated between
`alembic.ini` and the app config. Verified two ways: (1) the full test suite against a
database built *purely* by `alembic upgrade head` with `AUTO_CREATE_TABLES=false` (SQLite),
so `create_all` never ran and couldn't paper over a gap; (2) both migrations applied cleanly
to a **real local PostgreSQL 16 instance** (all 15 tables incl. `alembic_version` present),
with the full account→catalog→project→quote→line-item→calculate→generate-document→public-
share→accept flow run against it end to end and producing correct numbers. What's *not*
verified is your actual production Postgres (a managed instance, different version/config,
network latency, connection pooling under load) — the schema and migration path are proven
sound, but do the same run against your real target before trusting it blind.
When you change a model, generate the next migration the same way:
```
alembic revision --autogenerate -m "describe the change"
```
Always read the generated file before applying it — autogenerate is a good first draft,
not a guarantee (it won't detect every kind of change, e.g. some column renames look like
a drop + add unless you edit the migration by hand).

## Asset storage
Local disk is the default — uploaded logos land under `uploaded_assets/logos/` and are
served from `/static`. For S3-compatible object storage (AWS S3, MinIO, DigitalOcean
Spaces, ...), set:
- `STORAGE_BACKEND=s3`, `S3_BUCKET` (required)
- `S3_REGION`, `S3_ENDPOINT_URL` (for non-AWS S3-compatible services)
- `S3_PUBLIC_BASE_URL` — a public bucket URL or CDN in front of one. **Required for logos to
  actually render**: `DocTemplate.logo_url` is a plain property (no per-request AWS calls),
  so without this it just returns the raw `s3://bucket/key` URI, which no `<img>` tag can load.

boto3 is only imported inside `S3Storage` (`app/storage.py`) — a local-only deployment never
needs it installed or configured. I verified `S3Storage`'s key naming, `put_object` call
shape, and `url_for` logic with a mocked `boto3.client`, but **have not run it against a
real AWS account or bucket** — no AWS credentials are available in this environment.

## Deployment
`Dockerfile` builds the API (installs `psycopg2-binary`, runs `alembic upgrade head` then
`uvicorn` on container start — see its comments for the multi-instance caveat).
`docker-compose.yml` runs it alongside a real Postgres container for local testing; copy
`.env.example` to `.env` first (at minimum set `JWT_SECRET_KEY`), then:
```
docker compose up --build
```
No Docker available? `Procfile` gives the same two commands (`release` / `web`) for
PaaS providers that run a Procfile directly (Railway, Render, Heroku-style). The frontend
(`frontend/`) is a static build (`npm run build` → `frontend/dist/`) — deploy it to any
static host (Vercel, Netlify, S3+CloudFront, ...) pointed at the deployed API via
`VITE_API_BASE_URL`, and set `CORS_ORIGINS` on the backend to that static host's origin.

I don't have a cloud provider account in this environment, so none of this has been pushed
to a live URL — but the container/schema path itself is real: I ran actual PostgreSQL 16
locally (portable binaries, no Docker needed for that part) and applied both migrations to
it directly, then ran the full account→quote→accept flow against it end to end (see
"Migrations & production DB" above). What's untested is the *deployment mechanics*
specifically — building this exact Dockerfile, docker-compose networking, and whatever your
chosen host does differently.

## Next steps to production
The remaining items all share one shape: the code exists and is tested as far as this
environment allows, but each needs one real run against a live external system this
sandbox doesn't have credentials for.
1. A real Stripe test-mode run of `create_checkout_session` (see "Billing" above).
2. A real AWS/S3-compatible run of `S3Storage` — upload a real logo, confirm it renders via
   `S3_PUBLIC_BASE_URL` (see "Asset storage" above).
3. Actually building and running the `Dockerfile` / `docker-compose.yml` — no Docker is
   available in this environment, so they're written correctly but never executed.
4. Owner-vs-editor permission split is enforced for team management and billing only (see
   `require_owner` in `app/auth.py`) — everything else (catalog, projects, quotes, branding)
   is open to any team member by design; extend the same pattern if you want more locked down.
