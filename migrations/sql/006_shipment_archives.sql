CREATE TABLE shipment_archives (
 shipment_id uuid PRIMARY KEY REFERENCES consignments(id) ON DELETE RESTRICT,
 archived_by uuid NOT NULL REFERENCES admin_users(id) ON DELETE RESTRICT,
 archived_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot) = 'object')
);
CREATE INDEX ix_shipment_archives_archived_at ON shipment_archives(archived_at);
ALTER TABLE shipment_archives ENABLE ROW LEVEL SECURITY;
CREATE FUNCTION gram_archive_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
DECLARE who uuid; shipment uuid;
BEGIN
 who := nullif(current_setting('gram.actor_id',true),'')::uuid;
 IF NOT EXISTS(SELECT 1 FROM admin_users WHERE id=who AND role='administrator' AND active AND supabase_user_id IS NOT NULL)
 THEN RAISE EXCEPTION 'Active administrator required to archive or restore'; END IF;
 IF TG_OP NOT IN ('INSERT','DELETE') THEN RAISE EXCEPTION 'Archive snapshots are immutable'; END IF;
 shipment := CASE WHEN TG_OP='DELETE' THEN OLD.shipment_id ELSE NEW.shipment_id END;
 PERFORM id FROM consignments WHERE id=shipment FOR UPDATE;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 NEW.archived_by := who; NEW.archived_at := clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER archive_guard BEFORE INSERT OR UPDATE OR DELETE ON shipment_archives FOR EACH ROW EXECUTE FUNCTION gram_archive_guard();
CREATE TRIGGER archive_truncate_guard BEFORE TRUNCATE ON shipment_archives FOR EACH STATEMENT EXECUTE FUNCTION gram_archive_guard();
CREATE FUNCTION gram_archived_shipment_readonly() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM shipment_archives WHERE shipment_id=OLD.id)
 THEN RAISE EXCEPTION 'Restore archived shipment before changing it'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER archived_shipment_readonly BEFORE UPDATE ON consignments FOR EACH ROW EXECUTE FUNCTION gram_archived_shipment_readonly();
REVOKE ALL ON shipment_archives FROM PUBLIC;
REVOKE ALL ON FUNCTION gram_archive_guard(),gram_archived_shipment_readonly() FROM PUBLIC;
DO $$ DECLARE r text; BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN EXECUTE format('REVOKE ALL ON shipment_archives FROM %I',r); END IF;
 END LOOP;
END $$;
