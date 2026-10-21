-- Phase 0 sandbox schema + synthetic data + read-only role.
-- Runs once, as the postgres superuser, on first container init.
-- All data here is FAKE (no real people). Mirrors the shape of the loan
-- register so we can exercise masking, the read-only guard, and the cost gate.

CREATE SCHEMA IF NOT EXISTS loan;

CREATE TABLE loan.student_loan_register (
    reg_id                   bigserial PRIMARY KEY,
    status                   text NOT NULL,
    created_date             timestamptz NOT NULL,
    updated_date             timestamptz,
    borrower_cid             char(13),            -- PII (fake)
    borrower_first_name      text,                -- PII (fake)
    borrower_last_name       text,                -- PII (fake)
    borrower_dob             date,                -- PII (fake)
    borrower_email           text,                -- PII (fake)
    borrower_phone           text,                -- PII (fake)
    borrower_address_province text,               -- PII-ish (fake)
    univ_id                  int,
    student_id               text,
    major_name               text,                -- NOT PII (category)
    borrower_gpa             numeric(3,2),
    loan_tuition_term1       numeric(12,2),
    loan_type_living         numeric(12,2),
    eef_status               text
);
COMMENT ON TABLE loan.student_loan_register IS 'Synthetic loan registrations (sandbox)';

-- 500 synthetic rows; status cycles so PRE_CHECK (g % 5 = 3) ~ 100 rows.
INSERT INTO loan.student_loan_register (
    status, created_date, updated_date, borrower_cid, borrower_first_name,
    borrower_last_name, borrower_dob, borrower_email, borrower_phone,
    borrower_address_province, univ_id, student_id, major_name, borrower_gpa,
    loan_tuition_term1, loan_type_living, eef_status)
SELECT
    (ARRAY['APPROVED','SUBMITTED','PENDING','PRE_CHECK','REJECTED'])[1 + (g % 5)],
    now() - (g || ' hours')::interval,
    now() - ((g / 2) || ' hours')::interval,
    lpad((1100000000000 + g)::text, 13, '0'),
    (ARRAY['Somchai','Suda','Anan','Nok','Preecha','Malee'])[1 + (g % 6)],
    (ARRAY['Jaidee','Sukjai','Phonphan','Rakchart','Boonmee'])[1 + (g % 5)],
    date '2000-01-01' + (g % 3000),
    'user' || g || '@example.test',
    '08' || lpad((g % 100000000)::text, 8, '0'),
    (ARRAY['Bangkok','Chiang Mai','Khon Kaen','Songkhla','Nonthaburi'])[1 + (g % 5)],
    1 + (g % 20),
    'STD' || lpad(g::text, 6, '0'),
    (ARRAY['Computer Science','Nursing','Law','Engineering','Accounting'])[1 + (g % 5)],
    round((2 + random() * 2)::numeric, 2),
    round((10000 + random() * 40000)::numeric, 2),
    round((2000 + random() * 4000)::numeric, 2),
    (ARRAY['NONE','PENDING','PASSED'])[1 + (g % 3)]
FROM generate_series(1, 500) AS g;

-- A PII-redacted view = the production-grade boundary (see note below).
CREATE VIEW loan.v_register_safe AS
SELECT reg_id, status, created_date, updated_date, univ_id, student_id,
       major_name, borrower_gpa, loan_tuition_term1, loan_type_living,
       borrower_address_province, eef_status
FROM loan.student_loan_register;
COMMENT ON VIEW loan.v_register_safe IS 'Loan register without PII columns';

-- Read-only role db-mcp connects as.
CREATE ROLE mcp_ro LOGIN PASSWORD 'mcp_ro';
GRANT CONNECT ON DATABASE sandbox TO mcp_ro;
GRANT USAGE ON SCHEMA loan TO mcp_ro;

-- SANDBOX: grant the base table so app-layer auto-masking can be demonstrated.
-- PRODUCTION: grant ONLY the safe view (next line) and NOT the base table, so
-- PII is unreachable even if app masking is bypassed — that is the real wall.
GRANT SELECT ON loan.student_loan_register TO mcp_ro;
GRANT SELECT ON loan.v_register_safe TO mcp_ro;
