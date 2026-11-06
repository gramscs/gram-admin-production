-- REVIEW FIRST. Do not run after any real issuance, cancellation or counter approval.
-- Run with the original schema search_path. Restore original grants separately
-- from admin_migration_runs.source_schema.original_consignments_acl after review.
BEGIN;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT pg_advisory_xact_lock(723910042005);
LOCK TABLE consignments, issuing_locations IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM consignments WHERE lr_status <> 'legacy')
 OR EXISTS(SELECT 1 FROM lr_audit_log)
 OR EXISTS(SELECT 1 FROM lr_sequences)
 OR EXISTS(SELECT 1 FROM issuing_locations WHERE reconciliation_digest IS NOT NULL)
 OR EXISTS(SELECT 1 FROM lr_requests)
 OR EXISTS(SELECT 1 FROM lr_imports) THEN
 RAISE EXCEPTION 'LR state exists. Downgrade refused; preserve history and use a reviewed forward fix.';
 END IF;
END $$;
DROP TRIGGER lr_identity_guard ON consignments;
DROP TRIGGER lr_audit ON consignments;
DROP TRIGGER lr_shipment_truncate ON consignments;
DROP TRIGGER lr_existing_audit_immutable ON audit_logs;
DROP TRIGGER lr_existing_audit_truncate ON audit_logs;
DROP TABLE lr_audit_log, lr_requests, lr_imports;
DROP TABLE lr_sequences;
ALTER TABLE consignments DROP COLUMN location_id, DROP COLUMN lr_sequence,
 DROP COLUMN financial_year, DROP COLUMN created_at, DROP COLUMN lr_issued_at,
 DROP COLUMN lr_status, DROP COLUMN cancelled_at, DROP COLUMN cancelled_by,
 DROP COLUMN cancellation_reason;
DROP TABLE issuing_locations;
DROP FUNCTION gram_lr_audit(), gram_lr_identity_guard(), gram_lr_location_guard(),
 gram_lr_private_guard(), gram_audit_immutable(), gram_lr_financial_year(timestamptz);
DELETE FROM admin_migration_runs WHERE version = '005_centralized_lr';
COMMIT;
