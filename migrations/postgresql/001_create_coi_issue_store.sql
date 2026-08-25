BEGIN;

CREATE SCHEMA IF NOT EXISTS ah_app;

CREATE TABLE IF NOT EXISTS ah_app.current_issue (
    go_no text PRIMARY KEY,
    issue_revision integer NOT NULL CHECK (issue_revision >= 1),
    sync_revision integer NOT NULL DEFAULT 0 CHECK (sync_revision >= 0),
    issued_at timestamptz NOT NULL,
    issued_by text NOT NULL,
    last_synced_at timestamptz,
    last_synced_by text,
    row_count integer NOT NULL CHECK (row_count >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS ah_app.current_issue_row (
    go_no text NOT NULL REFERENCES ah_app.current_issue(go_no) ON DELETE CASCADE,
    internal_row_id uuid NOT NULL,
    row_no integer NOT NULL CHECK (row_no >= 1),
    brand text,
    ppo_no text,
    fabric_type text,
    color_code text,
    color_desc text,
    fabric_color text,
    job_order_no text,
    lot_no text,
    size_code text,
    minus_pct numeric,
    plus_pct numeric,
    qty_pcs numeric,
    buyer_po_delivery_date text,
    net_yy numeric,
    ppo_yy numeric,
    marker_yy numeric,
    required_qty_yds numeric,
    received_qty_ppo numeric,
    on_the_way_qty_yds numeric,
    allocate_qty_yds numeric,
    shortage_qty_yds numeric,
    ah_allocate_qty_yds numeric,
    allocate_pct numeric,
    etd_fabric text,
    user_remark text,
    ppo_order_total_yds numeric,
    sample_status text,
    last_modified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (go_no, internal_row_id),
    UNIQUE (go_no, row_no)
);

CREATE INDEX IF NOT EXISTS ix_current_issue_row_ppo_no
    ON ah_app.current_issue_row (ppo_no);
CREATE INDEX IF NOT EXISTS ix_current_issue_row_job_order_no
    ON ah_app.current_issue_row (job_order_no);
CREATE INDEX IF NOT EXISTS ix_current_issue_row_color_code
    ON ah_app.current_issue_row (color_code);
CREATE INDEX IF NOT EXISTS ix_current_issue_row_last_modified_at
    ON ah_app.current_issue_row (last_modified_at);

CREATE TABLE IF NOT EXISTS ah_app.issue_audit_log (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    go_no text NOT NULL,
    action text NOT NULL CHECK (
        action IN ('INITIAL_ISSUE', 'REISSUE', 'AUTO_SYNC_EDIT', 'SOURCE_REFRESH_APPLIED', 'NO_CHANGE')
    ),
    issue_revision integer NOT NULL,
    sync_revision integer NOT NULL,
    issued_by text NOT NULL,
    issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    added_row_count integer NOT NULL DEFAULT 0,
    removed_row_count integer NOT NULL DEFAULT 0,
    changed_row_count integer NOT NULL DEFAULT 0,
    changes jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_issue_audit_log_go_issued_at
    ON ah_app.issue_audit_log (go_no, issued_at DESC);
CREATE INDEX IF NOT EXISTS ix_issue_audit_log_user_issued_at
    ON ah_app.issue_audit_log (issued_by, issued_at DESC);

CREATE OR REPLACE VIEW ah_app.v_current_issue_rows AS
SELECT
    i.go_no,
    i.issue_revision,
    i.sync_revision,
    i.issued_at,
    i.issued_by,
    i.last_synced_at,
    i.last_synced_by,
    i.row_count,
    i.created_at,
    i.updated_at,
    r.row_no,
    r.brand,
    r.ppo_no,
    r.fabric_type,
    r.color_code,
    r.color_desc,
    r.fabric_color,
    r.job_order_no,
    r.lot_no,
    r.size_code,
    r.minus_pct,
    r.plus_pct,
    r.qty_pcs,
    r.buyer_po_delivery_date,
    r.net_yy,
    r.ppo_yy,
    r.marker_yy,
    r.required_qty_yds,
    r.received_qty_ppo,
    r.on_the_way_qty_yds,
    r.allocate_qty_yds,
    r.shortage_qty_yds,
    r.ah_allocate_qty_yds,
    r.allocate_pct,
    r.etd_fabric,
    r.user_remark,
    r.ppo_order_total_yds,
    r.sample_status,
    r.last_modified_at
FROM ah_app.current_issue i
JOIN ah_app.current_issue_row r ON r.go_no = i.go_no;

GRANT USAGE ON SCHEMA ah_app TO "harry.le";
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ah_app.current_issue, ah_app.current_issue_row, ah_app.issue_audit_log
    TO "harry.le";
GRANT USAGE, SELECT ON SEQUENCE ah_app.issue_audit_log_audit_id_seq TO "harry.le";
GRANT SELECT ON ah_app.v_current_issue_rows TO "harry.le";

COMMIT;
