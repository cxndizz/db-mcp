-- Bank-app demo schema + synthetic seed for the db-mcp sandbox (PostgreSQL).
-- All data is FAKE. Safe to run repeatedly: it drops & recreates schema `bank`.
--   docker exec -i dbmcp-sandbox-pg psql -U postgres -d sandbox < bank_schema_seed.sql
--
-- Volumes: ~30 branches, 2,000 customers, ~3,000 accounts, ~2,500 cards,
--          ~60,000 transactions, ~600 loans, ~7,000 loan payments.

DROP SCHEMA IF EXISTS bank CASCADE;
CREATE SCHEMA bank;
SET search_path TO bank;

-- ---------------------------------------------------------------- branches
CREATE TABLE branches (
    branch_id    serial PRIMARY KEY,
    code         text UNIQUE NOT NULL,
    name         text NOT NULL,
    province     text NOT NULL,
    opened_date  date NOT NULL
);
INSERT INTO branches (code, name, province, opened_date)
SELECT 'BR' || lpad(g::text, 4, '0'),
       'สาขา ' || (ARRAY['สีลม','อโศก','ลาดพร้าว','รัชดา','บางนา','ปิ่นเกล้า','รามคำแหง','เซ็นทรัล',
                         'ท่าพระ','งามวงศ์วาน','แจ้งวัฒนะ','พระราม 9'])[1 + (g % 12)] || ' ' || g,
       (ARRAY['กรุงเทพมหานคร','นนทบุรี','เชียงใหม่','ขอนแก่น','ชลบุรี','สงขลา','นครราชสีมา','ภูเก็ต'])[1 + (g % 8)],
       date '2008-01-01' + (g * 97 % 5000)
FROM generate_series(1, 30) AS g;

-- --------------------------------------------------------------- customers
CREATE TABLE customers (
    customer_id     bigserial PRIMARY KEY,
    national_id     char(13) NOT NULL,               -- PII
    prefix          text,
    first_name      text NOT NULL,                   -- PII
    last_name       text NOT NULL,                   -- PII
    dob             date,                            -- PII
    email           text,                            -- PII
    phone           text,                            -- PII
    address         text,                            -- PII
    province        text,
    occupation      text,
    monthly_income  numeric(12,2),                   -- PII (financial)
    risk_level      text,                            -- LOW / MEDIUM / HIGH
    kyc_status      text,                            -- VERIFIED / PENDING / REJECTED
    created_at      timestamptz NOT NULL
);
INSERT INTO customers (national_id, prefix, first_name, last_name, dob, email, phone,
                       address, province, occupation, monthly_income, risk_level, kyc_status, created_at)
SELECT
    lpad((1100000000000 + g)::text, 13, '0'),
    (ARRAY['นาย','นาง','นางสาว'])[1 + (g % 3)],
    (ARRAY['Somchai','Suda','Anan','Nok','Preecha','Malee','Kittipong','Ratana','Chaiwat','Pim'])[1 + (g % 10)],
    (ARRAY['Jaidee','Sukjai','Phonphan','Rakchart','Boonmee','Srisuk','Wongsa','Thongdee'])[1 + (g % 8)],
    date '1965-01-01' + (g * 7 % 16000),
    'customer' || g || '@example.test',
    '08' || lpad((g * 31 % 100000000)::text, 8, '0'),
    (g % 199 + 1)::text || ' ถนนสุขุมวิท',
    (ARRAY['กรุงเทพมหานคร','นนทบุรี','เชียงใหม่','ขอนแก่น','ชลบุรี','สงขลา','นครราชสีมา','ภูเก็ต'])[1 + (g % 8)],
    (ARRAY['พนักงานบริษัท','ข้าราชการ','ค้าขาย','เกษตรกร','รับจ้าง','ธุรกิจส่วนตัว','นักศึกษา'])[1 + (g % 7)],
    round((15000 + random() * 185000)::numeric, 2),
    (ARRAY['LOW','LOW','LOW','MEDIUM','MEDIUM','HIGH'])[1 + (g % 6)],
    (ARRAY['VERIFIED','VERIFIED','VERIFIED','PENDING','REJECTED'])[1 + (g % 5)],
    now() - ((g * 53 % 1200) || ' days')::interval
FROM generate_series(1, 2000) AS g;

-- ---------------------------------------------------------------- accounts
CREATE TABLE accounts (
    account_id    bigserial PRIMARY KEY,
    account_no    text UNIQUE NOT NULL,
    customer_id   bigint NOT NULL REFERENCES customers(customer_id),
    branch_id     int NOT NULL REFERENCES branches(branch_id),
    account_type  text NOT NULL,                     -- SAVINGS / CURRENT / FIXED
    currency      text NOT NULL DEFAULT 'THB',
    balance       numeric(14,2) NOT NULL,
    status        text NOT NULL,                     -- ACTIVE / DORMANT / CLOSED
    opened_at     timestamptz NOT NULL
);
INSERT INTO accounts (account_no, customer_id, branch_id, account_type, balance, status, opened_at)
SELECT
    lpad((1000000000 + g)::text, 10, '0'),
    1 + (g % 2000),
    1 + (g % 30),
    (ARRAY['SAVINGS','SAVINGS','SAVINGS','CURRENT','FIXED'])[1 + (g % 5)],
    round((random() * 2000000)::numeric, 2),
    (ARRAY['ACTIVE','ACTIVE','ACTIVE','ACTIVE','DORMANT','CLOSED'])[1 + (g % 6)],
    now() - ((g * 37 % 1100) || ' days')::interval
FROM generate_series(1, 3000) AS g;

-- ------------------------------------------------------------------- cards
CREATE TABLE cards (
    card_id       bigserial PRIMARY KEY,
    account_id    bigint NOT NULL REFERENCES accounts(account_id),
    card_no       char(16) NOT NULL,                 -- PII
    card_type     text NOT NULL,                     -- DEBIT / CREDIT
    credit_limit  numeric(12,2),
    expiry_date   date NOT NULL,
    status        text NOT NULL,                     -- ACTIVE / BLOCKED / EXPIRED
    issued_at     timestamptz NOT NULL
);
INSERT INTO cards (account_id, card_no, card_type, credit_limit, expiry_date, status, issued_at)
SELECT
    1 + (g % 3000),
    lpad((4000000000000000 + g * 7)::text, 16, '0'),
    (ARRAY['DEBIT','DEBIT','CREDIT'])[1 + (g % 3)],
    CASE WHEN g % 3 = 2 THEN round((20000 + random() * 480000)::numeric, 2) ELSE NULL END,
    date '2026-01-01' + (g % 1500),
    (ARRAY['ACTIVE','ACTIVE','ACTIVE','BLOCKED','EXPIRED'])[1 + (g % 5)],
    now() - ((g * 29 % 1000) || ' days')::interval
FROM generate_series(1, 2500) AS g;

-- ------------------------------------------------------------ transactions
CREATE TABLE transactions (
    txn_id              bigserial PRIMARY KEY,
    account_id          bigint NOT NULL REFERENCES accounts(account_id),
    txn_type            text NOT NULL,               -- DEPOSIT/WITHDRAW/TRANSFER/PAYMENT/FEE
    amount              numeric(14,2) NOT NULL,
    balance_after       numeric(14,2),
    channel             text NOT NULL,               -- ATM/MOBILE/BRANCH/INTERNET/POS
    counterparty_account text,
    counterparty_name    text,                       -- PII-ish
    description         text,
    status              text NOT NULL,               -- SUCCESS / FAILED / PENDING
    created_at          timestamptz NOT NULL
);
INSERT INTO transactions (account_id, txn_type, amount, balance_after, channel,
                          counterparty_account, counterparty_name, description, status, created_at)
SELECT
    1 + (g % 3000),
    (ARRAY['DEPOSIT','WITHDRAW','TRANSFER','PAYMENT','PAYMENT','FEE'])[1 + (g % 6)],
    round((10 + random() * 50000)::numeric, 2),
    round((random() * 500000)::numeric, 2),
    (ARRAY['ATM','MOBILE','MOBILE','INTERNET','BRANCH','POS'])[1 + (g % 6)],
    lpad((1000000000 + (g * 13 % 3000))::text, 10, '0'),
    (ARRAY['ร้านสะดวกซื้อ','การไฟฟ้า','โอนบุคคล','ค่าน้ำ','Shopee','Lazada','ปั๊มน้ำมัน','ร้านอาหาร'])[1 + (g % 8)],
    (ARRAY['ชำระบิล','โอนเงิน','ถอนเงิน','ฝากเงิน','ซื้อสินค้า','ค่าธรรมเนียม'])[1 + (g % 6)],
    (ARRAY['SUCCESS','SUCCESS','SUCCESS','SUCCESS','FAILED','PENDING'])[1 + (g % 6)],
    now() - ((g * 11 % 730) || ' days')::interval - ((g % 1440) || ' minutes')::interval
FROM generate_series(1, 60000) AS g;

-- ------------------------------------------------------------------- loans
CREATE TABLE loans (
    loan_id        bigserial PRIMARY KEY,
    customer_id    bigint NOT NULL REFERENCES customers(customer_id),
    loan_type      text NOT NULL,                    -- PERSONAL/HOME/AUTO/CREDIT
    principal      numeric(14,2) NOT NULL,
    interest_rate  numeric(5,2) NOT NULL,
    term_months    int NOT NULL,
    outstanding    numeric(14,2) NOT NULL,
    status         text NOT NULL,                    -- ACTIVE/CLOSED/OVERDUE/REJECTED
    disbursed_at   date
);
INSERT INTO loans (customer_id, loan_type, principal, interest_rate, term_months, outstanding, status, disbursed_at)
SELECT
    1 + (g % 2000),
    (ARRAY['PERSONAL','HOME','AUTO','CREDIT'])[1 + (g % 4)],
    round((50000 + random() * 4950000)::numeric, 2) AS principal,
    round((4 + random() * 18)::numeric, 2),
    (ARRAY[12,24,36,48,60,120,240])[1 + (g % 7)],
    round((random() * 3000000)::numeric, 2),
    (ARRAY['ACTIVE','ACTIVE','ACTIVE','CLOSED','OVERDUE','REJECTED'])[1 + (g % 6)],
    date '2019-01-01' + (g * 97 % 2500)
FROM generate_series(1, 600) AS g;

-- ----------------------------------------------------------- loan_payments
CREATE TABLE loan_payments (
    payment_id     bigserial PRIMARY KEY,
    loan_id        bigint NOT NULL REFERENCES loans(loan_id),
    installment_no int NOT NULL,
    due_date       date NOT NULL,
    amount         numeric(12,2) NOT NULL,
    paid_date      date,
    status         text NOT NULL                     -- PAID / DUE / OVERDUE
);
INSERT INTO loan_payments (loan_id, installment_no, due_date, amount, paid_date, status)
SELECT
    l.loan_id,
    n AS installment_no,
    (l.disbursed_at + (n || ' months')::interval)::date,
    round((l.principal / GREATEST(l.term_months,1) * (1 + l.interest_rate/100))::numeric, 2),
    CASE WHEN n <= 6 THEN (l.disbursed_at + (n || ' months')::interval)::date + (random()*5)::int ELSE NULL END,
    (ARRAY['PAID','PAID','PAID','PAID','PAID','PAID','DUE','OVERDUE'])[1 + (n % 8)]
FROM loans l
CROSS JOIN LATERAL generate_series(1, LEAST(l.term_months, 12)) AS n
WHERE l.status <> 'REJECTED';

-- ------------------------------------------------------------------- indexes
CREATE INDEX idx_accounts_customer ON accounts(customer_id);
CREATE INDEX idx_txn_account       ON transactions(account_id);
CREATE INDEX idx_txn_created       ON transactions(created_at);
CREATE INDEX idx_txn_type          ON transactions(txn_type);
CREATE INDEX idx_cards_account     ON cards(account_id);
CREATE INDEX idx_loans_customer    ON loans(customer_id);
CREATE INDEX idx_loanpay_loan      ON loan_payments(loan_id);

-- ------------------------------------------------- PII-free view (prod pattern)
CREATE VIEW v_customers_safe AS
SELECT customer_id, prefix, province, occupation, risk_level, kyc_status, created_at
FROM customers;

-- ------------------------------------------------------------- grant read-only
GRANT USAGE ON SCHEMA bank TO mcp_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA bank TO mcp_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA bank GRANT SELECT ON TABLES TO mcp_ro;

ANALYZE;

-- quick sanity counts
SELECT 'branches' AS t, count(*) FROM branches
UNION ALL SELECT 'customers', count(*) FROM customers
UNION ALL SELECT 'accounts', count(*) FROM accounts
UNION ALL SELECT 'cards', count(*) FROM cards
UNION ALL SELECT 'transactions', count(*) FROM transactions
UNION ALL SELECT 'loans', count(*) FROM loans
UNION ALL SELECT 'loan_payments', count(*) FROM loan_payments;
