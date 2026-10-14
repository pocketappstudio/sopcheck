-- Smoke test for db/schema.sql (run as superuser on an empty DB after applying the schema):
--   psql -v ON_ERROR_STOP=1 -f schema.sql && psql -f test_schema.sql
\set ON_ERROR_STOP 0
INSERT INTO companies (id,name,slug,timezone) VALUES ('11111111-1111-1111-1111-111111111111','Acme','acme','America/Chicago'),
                                                     ('22222222-2222-2222-2222-222222222222','Globex','globex','America/New_York');
INSERT INTO users (id,company_id,email,password_hash,full_name,role) VALUES
 ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','m@acme.co','$2b$12$x','Acme Mgr','manager');
INSERT INTO documents (id,company_id,title,sha256,extraction_method,ocr_confidence) VALUES
 ('dddddddd-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','Acme LOTO',repeat('a',64),'ocr',95.3),
 ('dddddddd-0000-0000-0000-000000000002','22222222-2222-2222-2222-222222222222','Globex Opening',repeat('b',64),'text',NULL);
INSERT INTO parse_runs (id,company_id,document_id,parser,prompt_version,status,version_no,version_status) VALUES
 ('eeeeeeee-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','dddddddd-0000-0000-0000-000000000001','mock','sop-extract/1.1.0','succeeded',1,'published'),
 ('eeeeeeee-0000-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','dddddddd-0000-0000-0000-000000000001','manual_edit','sop-extract/1.1.0','succeeded',2,'draft');
\echo '-- expect ERROR: second draft for the same document'
INSERT INTO parse_runs (company_id,document_id,parser,prompt_version,status,version_no,version_status) VALUES
 ('11111111-1111-1111-1111-111111111111','dddddddd-0000-0000-0000-000000000001','manual_edit','sop-extract/1.1.0','succeeded',3,'draft');
\echo '-- expect ERROR: dismissed flag without a reason'
INSERT INTO safety_flags (company_id,document_id,parse_run_id,span_hash,span_text,coverage,score,resolution) VALUES
 ('11111111-1111-1111-1111-111111111111','dddddddd-0000-0000-0000-000000000001','eeeeeeee-0000-0000-0000-000000000002',repeat('f',32),'Never work above 50 volts','missing',0,'dismissed');
\echo '-- expect OK: dismissed flag with a reason'
INSERT INTO safety_flags (company_id,document_id,parse_run_id,span_hash,span_text,coverage,score,resolution,resolution_reason) VALUES
 ('11111111-1111-1111-1111-111111111111','dddddddd-0000-0000-0000-000000000001','eeeeeeee-0000-0000-0000-000000000002',repeat('f',32),'Never work above 50 volts','missing',0,'dismissed','covered by permit SOP');
\echo '-- RLS: tenant-scoped role sees only its own company'
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN CREATE ROLE app_user LOGIN; END IF; END $$;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_user;
SET ROLE app_user;
SET app.company_id = '11111111-1111-1111-1111-111111111111';
SELECT 'acme sees' AS who, title, extraction_method, ocr_confidence FROM documents;
SELECT 'acme flags' AS who, coverage, resolution, resolution_reason FROM safety_flags;
SET app.company_id = '22222222-2222-2222-2222-222222222222';
SELECT 'globex sees' AS who, title FROM documents;
SELECT 'globex flags' AS who, count(*) FROM safety_flags;
RESET ROLE;
