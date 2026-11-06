-- Review first. Restore archived shipments before removing archive support.
BEGIN;
SET LOCAL lock_timeout = '15s';
SELECT pg_advisory_xact_lock(723910042006);
LOCK TABLE shipment_archives IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM shipment_archives) THEN
  RAISE EXCEPTION 'Restore archived shipments before rolling back migration 006';
 END IF;
END $$;
DROP TRIGGER archived_shipment_readonly ON consignments;
DROP TABLE shipment_archives;
DROP FUNCTION gram_archive_guard(), gram_archived_shipment_readonly();
DELETE FROM admin_migration_runs WHERE version='006_shipment_archives';
COMMIT;
