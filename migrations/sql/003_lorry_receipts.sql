-- Supabase SQL Editor alternative for migration 003_lorry_receipts.py.
-- Run the entire file against the existing application project, as its schema owner.
-- Preserves shipments, files, document versions and Storage objects.
-- Requires the existing migration ledger; does not create or replace tables.
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT pg_advisory_xact_lock(723910042004);
LOCK TABLE shipment_documents IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE
    definition text;
    allowed_types text[];
    recorded_digest text;
    documents_before bigint;
    source_description jsonb;
    source_checksum text;
BEGIN
    IF to_regclass('admin_migration_runs') IS NULL THEN
        RAISE EXCEPTION 'Migration ledger missing. Use the Python migration instead.';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO definition
    FROM pg_constraint
    WHERE conrelid = 'shipment_documents'::regclass
      AND conname = 'ck_shipment_documents_valid_type' AND contype = 'c';
    IF definition IS NULL OR position('document_type' IN definition) = 0 THEN
        RAISE EXCEPTION 'Document type constraint is missing or incompatible. No records were changed.';
    END IF;
    allowed_types := ARRAY(SELECT DISTINCT matched[1]
        FROM regexp_matches(definition, $pattern$'([^']+)'$pattern$, 'g') AS matched
        ORDER BY matched[1]);
    -- Compare membership rather than sort order, which varies by database collation.
    IF NOT (allowed_types @> ARRAY['POD', 'invoice']
            AND allowed_types <@ ARRAY['POD', 'invoice', 'LR']) THEN
        RAISE EXCEPTION 'Unexpected document types: %. Review the constraint before migration.', allowed_types;
    END IF;
    SELECT target_digest INTO recorded_digest FROM admin_migration_runs
    WHERE version = '003_lorry_receipts';
    IF recorded_digest IS NOT NULL AND recorded_digest <> '65f396761d26f20489aaef86d55bcc060e216444f43c9c0aa98da5b351a9a949' THEN
        RAISE EXCEPTION 'Migration 003 differs from its recorded revision. No records were changed.';
    END IF;
    IF recorded_digest IS NOT NULL AND NOT ('LR' = ANY(allowed_types)) THEN
        RAISE EXCEPTION 'Migration 003 is recorded but its constraint was changed. Review the database.';
    END IF;
    SELECT count(*) INTO documents_before FROM shipment_documents;
    IF 'LR' = ANY(allowed_types) THEN
        source_description := '{"allowed_types": ["LR", "POD", "invoice"]}'::jsonb;
        source_checksum := '5ef34d7a6ba18c00b6944da07b900d7b47f09f64991abf8352eed49afacf89d6';
    ELSE
        source_description := '{"allowed_types": ["POD", "invoice"]}'::jsonb;
        source_checksum := 'f168ae029efa87b7b1c4dae0597b0b5e11d6d56610d6d84f77beea53cf454074';
        ALTER TABLE shipment_documents DROP CONSTRAINT ck_shipment_documents_valid_type;
        ALTER TABLE shipment_documents ADD CONSTRAINT ck_shipment_documents_valid_type
            CHECK (document_type IN ('POD', 'invoice', 'LR'));
    END IF;
    IF (SELECT count(*) FROM shipment_documents) <> documents_before THEN
        RAISE EXCEPTION 'Document preservation check failed. Migration rolled back.';
    END IF;
    ALTER TABLE admin_migration_runs ENABLE ROW LEVEL SECURITY;
    IF recorded_digest IS NULL THEN
        INSERT INTO admin_migration_runs
            (version, namespace, source_digest, target_digest, source_schema, counts, warnings, completed_at)
        VALUES ('003_lorry_receipts', '40dcd091-4f7b-5625-bd75-dd935b5915e9'::uuid, source_checksum,
                '65f396761d26f20489aaef86d55bcc060e216444f43c9c0aa98da5b351a9a949', source_description,
                jsonb_build_object('shipment_documents', jsonb_build_object('preserved_rows', documents_before)),
                '[]'::jsonb, now());
    END IF;
END;
$migration$;
COMMIT;

-- Verify the database accepts LR links and the migration is recorded.
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'shipment_documents'::regclass
  AND conname = 'ck_shipment_documents_valid_type';
SELECT version, completed_at FROM admin_migration_runs WHERE version = '003_lorry_receipts';
