-- =============================================================================
-- Universal SOP -> Technician Checklist : PostgreSQL production schema (PG 14+)
-- Hierarchy: companies -> documents -> parse_runs -> tasks
--            positions / sites / technicians (per company)
--            checklist_assignments (task-set distributed to a technician/shift)
--            submissions (+ submission_evidence)  -> audit_log
-- Every tenant-owned table carries company_id; Row-Level Security enforces isolation.
-- All timestamps are timestamptz (stored UTC); clients render them in the viewer's zone, shift windows use companies.timezone.
-- The prototype uses SQLite via SQLAlchemy (backend/app/models.py) with a compatible subset.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- fuzzy search on titles/positions
CREATE EXTENSION IF NOT EXISTS citext;     -- case-insensitive emails

-- ---------- Enumerations ----------------------------------------------------
CREATE TYPE verification_method AS ENUM
  ('Checkbox','Photo Upload','Numeric Input','Text Input','Signature','Dual Sign-off');
CREATE TYPE parse_status       AS ENUM ('queued','running','succeeded','failed','repaired');
CREATE TYPE document_status    AS ENUM ('uploaded','parsed','in_review','published','archived');
CREATE TYPE assignment_status  AS ENUM ('assigned','in_progress','completed','overdue','cancelled');
CREATE TYPE submission_status  AS ENUM ('completed','failed_check','skipped','pending_cosign');
CREATE TYPE evidence_type      AS ENUM ('checkbox','photo','numeric','text','signature','cosign');
CREATE TYPE user_role          AS ENUM ('admin','manager','technician','auditor');
CREATE TYPE version_status     AS ENUM ('draft','published','superseded');
CREATE TYPE extraction_method  AS ENUM ('text','pdf_text','ocr','mixed');
CREATE TYPE safety_coverage    AS ENUM ('covered','partial','missing');
CREATE TYPE safety_resolution  AS ENUM ('attached','task_created','dismissed');

-- ---------- Shared trigger: maintain updated_at -----------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

-- ---------- Tenancy ---------------------------------------------------------
CREATE TABLE companies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  slug          text NOT NULL UNIQUE,
  industry      text,                                  -- default industry hint for the LLM
  timezone      text NOT NULL DEFAULT 'UTC',           -- IANA zone (e.g. America/Chicago) for shift windows / 'today'
  settings      jsonb NOT NULL DEFAULT '{}'::jsonb,    -- e.g. model, variant, retention,
                                                       --   shift_windows: {"Shift Start": ["07:00","09:00"], ...} (company-local)
  -- billing (Gumroad memberships; limits come from server config, see backend/app/billing.py)
  plan                text NOT NULL DEFAULT 'trial' CHECK (plan IN ('trial','starter','team','expired')),
  trial_started_at    timestamptz NOT NULL DEFAULT now(),          -- trial = TRIAL_DAYS (14) from here
  license_key         text,                                        -- Gumroad license key (server-side only; API shows last 4)
  license_key_last4   text,
  license_verified_at timestamptz,                                 -- last successful Gumroad /v2/licenses/verify
  license_status      text,
  deletion_requested_at  timestamptz,
  deletion_scheduled_for timestamptz,   -- workspace locked (423) until purge
  deletion_requested_by  integer,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz                            -- soft delete
);

-- App users (managers, technicians, auditors). Auth itself lives in your IdP.
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  email         citext NOT NULL UNIQUE,                -- login id (globally unique)
  password_hash text,                                  -- bcrypt ($2b$, cost 12); NULL when SSO-only
  full_name     text NOT NULL,
  role          user_role NOT NULL DEFAULT 'technician',
  external_id   text,                                  -- IdP subject (SSO)
  active        boolean NOT NULL DEFAULT true,
  failed_logins int NOT NULL DEFAULT 0,                -- brute-force throttling
  locked_until  timestamptz,
  last_login_at timestamptz,
  terms_version     text,                              -- accepted Terms of Service / Privacy Policy version
  terms_accepted_at timestamptz,                       -- click-through acceptance time (UTC)
  terms_accept_ip   inet,
  deleted_at        timestamptz,       -- self-deleted: anonymized, login disabled
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, email)
);

-- Server-side session per issued JWT (jti) so logout / admin revocation is immediate.
-- Looked up before the tenant is known, so it is NOT under RLS (access only via the auth service role).
CREATE TABLE auth_sessions (
  id            text PRIMARY KEY,                      -- JWT jti
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_agent    text,
  ip_address    inet,
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  revoked_at    timestamptz
);

-- ---------- Normalized vocabularies (per company) ---------------------------
-- LLM output strings are mapped onto these; 'Unspecified' rows exist per company.
CREATE TABLE positions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  name          text NOT NULL,                         -- "Lead Electrician"
  aliases       text[] NOT NULL DEFAULT '{}',          -- synonyms seen in documents
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, name)
);

CREATE TABLE sites (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  parent_id     uuid REFERENCES sites(id) ON DELETE SET NULL,  -- facility > building > room
  name          text NOT NULL,                         -- "Server Room", "Patient Bedside"
  aliases       text[] NOT NULL DEFAULT '{}',
  geo           jsonb,                                 -- optional lat/lng / geofence
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, name)
);

CREATE TABLE timeframes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  label         text NOT NULL,                         -- "Shift Start", "Hourly", "Post-Operation"
  recurrence    text,                                  -- optional RFC 5545 RRULE
  sort_order    int  NOT NULL DEFAULT 100,
  UNIQUE (company_id, label)
);

CREATE TABLE technicians (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
  employee_code text,
  display_name  text NOT NULL,
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, employee_code)
);

-- Technicians may hold several qualified positions / work several sites.
CREATE TABLE technician_positions (
  technician_id uuid NOT NULL REFERENCES technicians(id) ON DELETE CASCADE,
  position_id   uuid NOT NULL REFERENCES positions(id)   ON DELETE CASCADE,
  company_id    uuid NOT NULL REFERENCES companies(id)   ON DELETE CASCADE,
  qualified_until date,                                  -- certification expiry
  PRIMARY KEY (technician_id, position_id)
);
CREATE TABLE technician_sites (
  technician_id uuid NOT NULL REFERENCES technicians(id) ON DELETE CASCADE,
  site_id       uuid NOT NULL REFERENCES sites(id)       ON DELETE CASCADE,
  company_id    uuid NOT NULL REFERENCES companies(id)   ON DELETE CASCADE,
  PRIMARY KEY (technician_id, site_id)
);

-- ---------- Documents & parsing --------------------------------------------
CREATE TABLE documents (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  title           text NOT NULL,
  doc_type        text NOT NULL DEFAULT 'SOP',          -- SOP | SOW | Work Instruction ...
  version_label   text,                                 -- company's own revision ("Rev C")
  supersedes_id   uuid REFERENCES documents(id),        -- revision chain
  source_filename text,
  mime_type       text,
  storage_uri     text,                                 -- s3://... original file
  sha256          char(64) NOT NULL,                    -- dedupe identical uploads
  raw_text        text,                                 -- extracted text (for re-parse/audit)
  page_count      int,
  extraction_method extraction_method NOT NULL DEFAULT 'text',
  ocr_confidence  numeric(5,2),                         -- mean tesseract word confidence 0-100 (OCR pages only)
  ocr_pages       jsonb,                                -- [{"page":1,"confidence":95.3,"words":168}]
  status          document_status NOT NULL DEFAULT 'uploaded',
  published_run_id uuid,                                -- FK added below (circular)
  published_at    timestamptz,
  published_by    uuid REFERENCES users(id),
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz,
  UNIQUE (company_id, sha256)
);

-- One row per checklist VERSION of a document: v1 comes from LLM extraction; manager edits on a
-- published version create v(n+1) as a draft copy (parent_run_id). Technicians only ever see the
-- published version; publishing marks the previous one superseded.
CREATE TABLE parse_runs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_no        int NOT NULL DEFAULT 1,
  version_status    version_status NOT NULL DEFAULT 'draft',
  parent_run_id     uuid REFERENCES parse_runs(id),
  status            parse_status NOT NULL DEFAULT 'queued',
  parser            text NOT NULL,                      -- 'llm' | 'mock' | 'manual_edit'
  model             text,                               -- e.g. 'gpt-4o-mini'
  prompt_version    text NOT NULL,                      -- 'sop-extract/1.0.0'
  chunk_count       int  NOT NULL DEFAULT 1,
  repair_attempts   int  NOT NULL DEFAULT 0,
  industry          text,
  document_summary  text,
  output_json       jsonb,                              -- validated checklist payload (exact schema)
  raw_output        text,                               -- unvalidated model text, for debugging
  validation_errors jsonb,                              -- list of errors per attempt
  safety_audit      jsonb,                              -- uncovered numbers/PPE/refs
  prompt_tokens     int,
  completion_tokens int,
  latency_ms        int,
  usage             jsonb,                         -- tokens + estimated USD (extraction + safety audit)
  started_at        timestamptz,
  finished_at       timestamptz,
  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, version_no)
);
-- at most one draft and one published version per document
CREATE UNIQUE INDEX uq_parse_runs_one_draft     ON parse_runs (document_id) WHERE version_status = 'draft';
CREATE UNIQUE INDEX uq_parse_runs_one_published ON parse_runs (document_id) WHERE version_status = 'published';
ALTER TABLE documents
  ADD CONSTRAINT documents_published_run_fk
  FOREIGN KEY (published_run_id) REFERENCES parse_runs(id) ON DELETE SET NULL;

-- Tasks are materialized from parse_runs.output_json. Raw strings are kept verbatim;
-- *_id columns hold the normalized mapping used for filtering/distribution.
CREATE TABLE tasks (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id          uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  document_id         uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  parse_run_id        uuid NOT NULL REFERENCES parse_runs(id) ON DELETE CASCADE,
  task_code           text NOT NULL,                    -- LLM task_id, e.g. 'T-001'
  sequence            int  NOT NULL,                    -- display / execution order
  task_title          text NOT NULL,
  description         text NOT NULL,
  assigned_position   text NOT NULL,                    -- raw LLM string
  site_requirement    text NOT NULL,
  timeframe           text NOT NULL,
  verification_method verification_method NOT NULL,
  position_id         uuid REFERENCES positions(id)  ON DELETE SET NULL,
  site_id             uuid REFERENCES sites(id)      ON DELETE SET NULL,
  timeframe_id        uuid REFERENCES timeframes(id) ON DELETE SET NULL,
  is_safety_critical  boolean NOT NULL DEFAULT false,   -- derived (WARNING/PPE/LOTO/threshold)
  numeric_min         numeric,                          -- optional acceptance range for Numeric Input
  numeric_max         numeric,
  numeric_unit        text,
  extended            jsonb,                            -- optional 'extended' variant payload
  origin_task_id      uuid REFERENCES tasks(id) ON DELETE SET NULL,  -- row copied from (versioning lineage)
  edited_by           uuid REFERENCES users(id),        -- manager edits before publish
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (parse_run_id, task_code)
);

-- ---------- Distribution ----------------------------------------------------
-- An assignment = a technician's checklist instance for a given date/shift, resolved from
-- filters (position/site/timeframe) against the document's published parse_run.
CREATE TABLE checklist_assignments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  parse_run_id    uuid NOT NULL REFERENCES parse_runs(id),
  technician_id   uuid NOT NULL REFERENCES technicians(id) ON DELETE CASCADE,
  position_id     uuid REFERENCES positions(id),
  site_id         uuid REFERENCES sites(id),
  timeframe_id    uuid REFERENCES timeframes(id),
  shift_date      date NOT NULL,
  due_at          timestamptz,
  status          assignment_status NOT NULL DEFAULT 'assigned',
  assigned_by     uuid REFERENCES users(id),
  completed_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (technician_id, parse_run_id, shift_date, timeframe_id)
);

-- Snapshot of which tasks belong to the assignment (so later doc edits don't mutate history).
CREATE TABLE assignment_tasks (
  assignment_id uuid NOT NULL REFERENCES checklist_assignments(id) ON DELETE CASCADE,
  task_id       uuid NOT NULL REFERENCES tasks(id),
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  sequence      int  NOT NULL,
  PRIMARY KEY (assignment_id, task_id)
);

-- ---------- Execution / compliance -----------------------------------------
-- One submission per task per assignment (re-submissions create a new row, prior is superseded).
CREATE TABLE submissions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  assignment_id   uuid REFERENCES checklist_assignments(id) ON DELETE CASCADE,
  task_id         uuid NOT NULL REFERENCES tasks(id),
  technician_id   uuid NOT NULL REFERENCES technicians(id),
  status          submission_status NOT NULL DEFAULT 'completed',
  -- denormalized evidence (fast reads); full detail in submission_evidence
  checked         boolean,
  numeric_value   numeric,
  numeric_unit    text,
  within_range    boolean,                              -- computed vs tasks.numeric_min/max
  text_value      text,
  photo_url       text,
  signature_url   text,                                 -- stored signature image / SVG
  user_id         uuid NOT NULL REFERENCES users(id),   -- authenticated submitter
  cosigner_id     uuid REFERENCES technicians(id),      -- Dual Sign-off second person
  cosigner_user_id uuid REFERENCES users(id),           -- co-signer re-authenticated with their own password
  cosigned_at     timestamptz,
  notes           text,
  device_info     jsonb,                                -- app version, device id
  geo             jsonb,                                -- lat/lng at submission
  submitted_at    timestamptz NOT NULL DEFAULT now(),
  client_submitted_at timestamptz,                      -- offline-first clock
  superseded_by   uuid REFERENCES submissions(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (cosigner_id IS NULL OR cosigner_id <> technician_id)  -- two different people
);

CREATE TABLE submission_evidence (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  submission_id   uuid NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  evidence_type   evidence_type NOT NULL,
  value_bool      boolean,
  value_numeric   numeric,
  value_text      text,
  file_uri        text,                                 -- photo / signature object
  file_sha256     char(64),                             -- tamper evidence
  captured_by     uuid REFERENCES technicians(id),
  captured_at     timestamptz NOT NULL DEFAULT now()
);

-- Hybrid safety-coverage audit: one row per safety-critical source span per checklist version.
CREATE TABLE safety_flags (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id           uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  document_id          uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  parse_run_id         uuid NOT NULL REFERENCES parse_runs(id) ON DELETE CASCADE,
  span_hash            char(32) NOT NULL,                -- sha256(normalized span)[:32]; resolutions carry across versions
  span_text            text NOT NULL,
  section              text,
  categories           text[] NOT NULL DEFAULT '{}',     -- warning, ppe, energy_isolation, threshold, regulatory, ...
  atoms                jsonb NOT NULL DEFAULT '[]',      -- normalized facts: quantities(+comparator), regs, PPE, literals
  detector             text NOT NULL DEFAULT 'rules',    -- rules | llm | rules+llm
  coverage             safety_coverage NOT NULL,
  score                numeric(5,1) NOT NULL,
  best_task_code       text,
  matched_atoms        jsonb NOT NULL DEFAULT '[]',
  missing_atoms        jsonb NOT NULL DEFAULT '[]',
  resolution           safety_resolution,
  resolution_reason    text,                             -- mandatory for 'dismissed' (CHECK below)
  resolution_task_code text,
  resolved_by          uuid REFERENCES users(id),
  resolved_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (parse_run_id, span_hash),
  CHECK (resolution IS DISTINCT FROM 'dismissed' OR length(coalesce(resolution_reason, '')) >= 5)
);

-- Append-only audit trail for every state-changing action (publish, edit, submit, cosign).
CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  actor_user_id uuid REFERENCES users(id),
  entity_type   text NOT NULL,                          -- 'document','task','submission',...
  entity_id     uuid NOT NULL,
  action        text NOT NULL,                          -- 'create','update','publish','submit'
  before_data   jsonb,
  after_data    jsonb,
  ip_address    inet,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------- Indexes ---------------------------------------------------------
CREATE INDEX idx_documents_company_status   ON documents (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX idx_parse_runs_document        ON parse_runs (document_id, created_at DESC);
-- Core distribution filters (position / site / timeframe), scoped to tenant:
CREATE INDEX idx_tasks_filter_ids           ON tasks (company_id, position_id, site_id, timeframe_id);
CREATE INDEX idx_tasks_filter_raw           ON tasks (company_id, assigned_position, site_requirement, timeframe);
CREATE INDEX idx_tasks_run_seq              ON tasks (parse_run_id, sequence);
CREATE INDEX idx_tasks_safety               ON tasks (company_id) WHERE is_safety_critical;
CREATE INDEX idx_tasks_title_trgm           ON tasks USING gin (task_title gin_trgm_ops);
CREATE INDEX idx_assign_tech_date           ON checklist_assignments (company_id, technician_id, shift_date);
CREATE INDEX idx_assign_status_due          ON checklist_assignments (company_id, status, due_at);
CREATE INDEX idx_submissions_assignment     ON submissions (assignment_id, task_id) WHERE superseded_by IS NULL;
CREATE INDEX idx_submissions_tech_time      ON submissions (company_id, technician_id, submitted_at DESC);
CREATE INDEX idx_evidence_submission        ON submission_evidence (submission_id);
CREATE INDEX idx_audit_entity               ON audit_log (company_id, entity_type, entity_id, created_at DESC);
CREATE INDEX idx_sessions_user              ON auth_sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_safety_flags_open          ON safety_flags (parse_run_id) WHERE coverage <> 'covered' AND resolution IS NULL;
CREATE INDEX idx_safety_flags_doc_hash      ON safety_flags (document_id, span_hash);

-- ---------- updated_at triggers ---------------------------------------------
CREATE TRIGGER trg_companies_upd   BEFORE UPDATE ON companies             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_upd       BEFORE UPDATE ON users                 FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_tech_upd        BEFORE UPDATE ON technicians           FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_documents_upd   BEFORE UPDATE ON documents             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_tasks_upd       BEFORE UPDATE ON tasks                 FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_assign_upd      BEFORE UPDATE ON checklist_assignments FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_runs_upd        BEFORE UPDATE ON parse_runs            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- Multi-tenant Row-Level Security --------------------------------
-- The API sets: SET app.company_id = '<uuid>' per request/transaction.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','positions','sites','timeframes','technicians',
    'technician_positions','technician_sites','documents','parse_runs','tasks',
    'checklist_assignments','assignment_tasks','submissions','submission_evidence','audit_log','safety_flags']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
      USING (company_id = current_setting('app.company_id', true)::uuid)
      WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)$p$, t);
  END LOOP;
END $$;

-- ---------- Convenience view: technician's open checklist -------------------
CREATE VIEW v_assignment_progress AS
SELECT a.id AS assignment_id, a.company_id, a.technician_id, a.shift_date, a.status,
       count(at.task_id)                                         AS total_tasks,
       count(s.id) FILTER (WHERE s.status = 'completed')          AS completed_tasks,
       round(100.0 * count(s.id) FILTER (WHERE s.status = 'completed')
             / NULLIF(count(at.task_id), 0), 1)                   AS pct_complete
FROM checklist_assignments a
JOIN assignment_tasks at ON at.assignment_id = a.id
LEFT JOIN submissions s  ON s.assignment_id = a.id AND s.task_id = at.task_id AND s.superseded_by IS NULL
GROUP BY a.id;

-- Minimal audit-safe record of a purged workspace: no names, logins, text or files.
CREATE TABLE deletion_tombstones (
  id                   serial PRIMARY KEY,
  company_id           integer NOT NULL,
  slug_sha256          char(64) NOT NULL,
  requested_at         timestamptz,
  requested_by_user_id integer,
  purged_at            timestamptz NOT NULL DEFAULT now(),
  counts               jsonb
);
CREATE INDEX deletion_tombstones_company_idx ON deletion_tombstones(company_id);
