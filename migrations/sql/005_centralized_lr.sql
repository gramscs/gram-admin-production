-- Applied transactionally by 005_centralized_lr.py; __SCHEMA__ is safely quoted.
CREATE TABLE issuing_locations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), location_name varchar(120) NOT NULL CHECK (trim(location_name) <> ''),
 location_code varchar(10) NOT NULL UNIQUE CHECK (location_code ~ '^[A-Z]{2,10}$'),
 is_active boolean NOT NULL DEFAULT true, reconciliation_digest varchar(64), reconciliation_reason text,
 opening_counters jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE lr_sequences (
 location_id uuid NOT NULL REFERENCES issuing_locations(id) ON DELETE RESTRICT,
 financial_year varchar(4) NOT NULL CHECK (financial_year ~ '^[0-9]{4}$'),
 last_sequence integer NOT NULL CHECK (last_sequence BETWEEN 0 AND 999999),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(location_id, financial_year)
);
ALTER TABLE consignments
 ADD COLUMN IF NOT EXISTS location_id uuid,
 ADD COLUMN IF NOT EXISTS lr_sequence integer, ADD COLUMN IF NOT EXISTS financial_year varchar(4),
 ADD COLUMN IF NOT EXISTS created_at timestamptz, ADD COLUMN IF NOT EXISTS lr_issued_at timestamptz,
 ADD COLUMN IF NOT EXISTS lr_status varchar(16) NOT NULL DEFAULT 'legacy',
 ADD COLUMN IF NOT EXISTS cancelled_at timestamptz, ADD COLUMN IF NOT EXISTS cancelled_by uuid,
 ADD COLUMN IF NOT EXISTS cancellation_reason text,
 ADD CONSTRAINT fk_lr_issuing_location FOREIGN KEY(location_id) REFERENCES issuing_locations(id) ON DELETE RESTRICT,
 ADD CONSTRAINT fk_lr_cancelled_by FOREIGN KEY(cancelled_by) REFERENCES admin_users(id) ON DELETE RESTRICT,
 ADD CONSTRAINT uq_lr_allocation UNIQUE(location_id, financial_year, lr_sequence),
 ADD CONSTRAINT ck_lr_identity CHECK (
 (lr_status = 'legacy' AND location_id IS NULL AND lr_sequence IS NULL AND financial_year IS NULL AND lr_issued_at IS NULL)
 OR (lr_status IN ('issued','cancelled') AND location_id IS NOT NULL AND lr_sequence IS NOT NULL AND lr_sequence BETWEEN 1 AND 999999
     AND financial_year IS NOT NULL AND lr_issued_at IS NOT NULL AND created_at IS NOT NULL)),
 ADD CONSTRAINT ck_lr_cancellation CHECK (
 (lr_status <> 'cancelled' AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)
 OR (lr_status = 'cancelled' AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL AND cancellation_reason IS NOT NULL AND length(trim(cancellation_reason)) BETWEEN 1 AND 1000));
ALTER TABLE consignments ALTER COLUMN created_at SET DEFAULT clock_timestamp();
CREATE INDEX ix_lr_register ON consignments(location_id, financial_year, lr_status);
CREATE TABLE lr_requests (
 actor_id uuid NOT NULL REFERENCES admin_users(id), request_key uuid NOT NULL, digest varchar(64) NOT NULL,
 response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(actor_id, request_key)
);
CREATE TABLE lr_imports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_id uuid NOT NULL REFERENCES admin_users(id),
 digest varchar(64) NOT NULL, rows jsonb NOT NULL, response jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(actor_id, digest)
);
CREATE TABLE lr_audit_log (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), shipment_id uuid NOT NULL REFERENCES consignments(id) ON DELETE RESTRICT,
 lr_number varchar(64) NOT NULL, action varchar(24) NOT NULL, actor_id uuid NOT NULL REFERENCES admin_users(id),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), before_values jsonb, after_values jsonb NOT NULL, reason text
);
CREATE INDEX ix_lr_audit_shipment ON lr_audit_log(shipment_id, recorded_at);

CREATE FUNCTION gram_lr_private_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME = 'lr_sequences' AND pg_trigger_depth() > 1 THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME = 'lr_audit_log' AND TG_OP = 'INSERT' AND pg_trigger_depth() > 1 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'LR counters and audit history cannot be edited directly';
END $$;
CREATE TRIGGER lr_counter_guard BEFORE INSERT OR UPDATE OR DELETE ON lr_sequences FOR EACH ROW EXECUTE FUNCTION gram_lr_private_guard();
CREATE TRIGGER lr_audit_guard BEFORE INSERT OR UPDATE OR DELETE ON lr_audit_log FOR EACH ROW EXECUTE FUNCTION gram_lr_private_guard();

CREATE FUNCTION gram_lr_location_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
DECLARE item record;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Deactivate issuing locations instead of deleting them'; END IF;
 IF TG_OP = 'UPDATE' THEN
   IF OLD.reconciliation_digest IS NOT NULL AND
      (NEW.location_code, NEW.reconciliation_digest, NEW.reconciliation_reason, NEW.opening_counters)
      IS DISTINCT FROM (OLD.location_code, OLD.reconciliation_digest, OLD.reconciliation_reason, OLD.opening_counters)
   THEN RAISE EXCEPTION 'Reconciled location identity and opening counters are immutable'; END IF;
   IF OLD.reconciliation_digest IS NULL AND NEW.reconciliation_digest IS NOT NULL THEN
     IF NEW.reconciliation_digest !~ '^[a-f0-9]{64}$' OR length(trim(coalesce(NEW.reconciliation_reason,''))) < 1 THEN
       RAISE EXCEPTION 'Reviewed historical digest and reason are required';
     END IF;
     FOR item IN SELECT key, value FROM jsonb_each_text(NEW.opening_counters) LOOP
       INSERT INTO lr_sequences(location_id, financial_year, last_sequence)
       VALUES(NEW.id, item.key, item.value::integer);
     END LOOP;
   END IF;
 END IF;
 NEW.updated_at := clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER lr_location_guard BEFORE UPDATE OR DELETE ON issuing_locations FOR EACH ROW EXECUTE FUNCTION gram_lr_location_guard();

CREATE FUNCTION gram_lr_financial_year(moment timestamptz) RETURNS varchar(4) LANGUAGE sql IMMUTABLE AS $$
 SELECT to_char((moment AT TIME ZONE 'Asia/Kolkata') - interval '3 months','YY') ||
        to_char((moment AT TIME ZONE 'Asia/Kolkata') + interval '9 months','YY')
$$;
CREATE FUNCTION gram_lr_identity_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
DECLARE loc issuing_locations%ROWTYPE; moment timestamptz; fy varchar(4); seq integer; who uuid; role_name text;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Issued and historical shipments cannot be deleted; cancel instead'; END IF;
 who := nullif(current_setting('gram.actor_id', true), '')::uuid;
 SELECT role INTO role_name FROM admin_users WHERE id = who AND active AND supabase_user_id IS NOT NULL;
 IF role_name IS NULL OR role_name NOT IN ('administrator','operator') THEN RAISE EXCEPTION 'Authorized LR operator required'; END IF;
 IF TG_OP = 'INSERT' THEN
   IF NEW.identifier_value IS NOT NULL OR NEW.lr_sequence IS NOT NULL OR NEW.financial_year IS NOT NULL
      OR NEW.lr_issued_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by IS NOT NULL
      OR NEW.cancellation_reason IS NOT NULL OR NEW.lr_status <> 'legacy' OR NEW.identifier_type <> 'LRN'
   THEN RAISE EXCEPTION 'GRAM generates LR identity automatically'; END IF;
   -- One transaction owns this location until commit. Capture time AFTER locking,
   -- so the FY and sequence agree even when a waiter crosses midnight on April 1.
   SELECT * INTO loc FROM issuing_locations WHERE id = NEW.location_id FOR UPDATE;
   IF NOT FOUND OR NOT loc.is_active OR loc.reconciliation_digest IS NULL THEN
     RAISE EXCEPTION 'Active, reconciled issuing location required'; END IF;
   moment := clock_timestamp();
   fy := gram_lr_financial_year(moment);
   INSERT INTO lr_sequences(location_id, financial_year, last_sequence) VALUES(loc.id, fy, 1)
   ON CONFLICT(location_id,financial_year) DO UPDATE SET last_sequence = lr_sequences.last_sequence + 1, updated_at = moment
   WHERE lr_sequences.last_sequence < 999999 RETURNING last_sequence INTO seq;
   IF seq IS NULL THEN RAISE EXCEPTION 'LR sequence capacity exhausted; contact an administrator'; END IF;
   NEW.identifier_value := loc.location_code || fy || lpad(seq::text,6,'0');
   NEW.identifier_type := 'LRN'; NEW.lr_sequence := seq; NEW.financial_year := fy;
   NEW.created_at := moment; NEW.lr_issued_at := moment; NEW.lr_status := 'issued';
 ELSE
   IF (NEW.id, NEW.identifier_value, NEW.identifier_type, NEW.location_id, NEW.financial_year, NEW.lr_sequence, NEW.lr_issued_at, NEW.created_at)
      IS DISTINCT FROM (OLD.id, OLD.identifier_value, OLD.identifier_type, OLD.location_id, OLD.financial_year, OLD.lr_sequence, OLD.lr_issued_at, OLD.created_at)
   THEN RAISE EXCEPTION 'LR identity and timestamps are immutable'; END IF;
   IF OLD.lr_status = 'cancelled' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Cancelled shipments are read-only'; END IF;
   IF NEW.lr_status IS DISTINCT FROM OLD.lr_status THEN
     IF NEW.lr_status <> 'cancelled' OR role_name <> 'administrator' OR length(trim(coalesce(NEW.cancellation_reason,''))) NOT BETWEEN 1 AND 1000
     THEN RAISE EXCEPTION 'Administrator and cancellation reason required'; END IF;
     NEW.cancelled_at := clock_timestamp(); NEW.cancelled_by := who;
   ELSIF (NEW.cancelled_at,NEW.cancelled_by,NEW.cancellation_reason) IS DISTINCT FROM (OLD.cancelled_at,OLD.cancelled_by,OLD.cancellation_reason) THEN
     RAISE EXCEPTION 'Use the cancellation workflow';
   END IF;
 END IF;
 RETURN NEW;
END $$;
-- Legacy cancellation retains its NULL location/sequence/FY; it is never renumbered.
ALTER TABLE consignments DROP CONSTRAINT ck_lr_identity;
ALTER TABLE consignments ADD CONSTRAINT ck_lr_identity CHECK (
 (location_id IS NULL AND lr_sequence IS NULL AND financial_year IS NULL AND lr_issued_at IS NULL AND lr_status IN ('legacy','cancelled'))
 OR (lr_status IN ('issued','cancelled') AND location_id IS NOT NULL AND lr_sequence IS NOT NULL AND lr_sequence BETWEEN 1 AND 999999
     AND financial_year IS NOT NULL AND lr_issued_at IS NOT NULL AND created_at IS NOT NULL));
CREATE TRIGGER lr_identity_guard BEFORE INSERT OR UPDATE OR DELETE ON consignments FOR EACH ROW EXECUTE FUNCTION gram_lr_identity_guard();
CREATE FUNCTION gram_lr_audit() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
BEGIN
 IF TG_OP = 'INSERT' OR NEW IS DISTINCT FROM OLD THEN
 INSERT INTO lr_audit_log(shipment_id,lr_number,action,actor_id,before_values,after_values,reason)
 VALUES(NEW.id, NEW.identifier_value,
 CASE WHEN TG_OP = 'INSERT' THEN 'issued' WHEN NEW.lr_status = 'cancelled' THEN 'cancelled' ELSE 'amended' END,
 nullif(current_setting('gram.actor_id',true),'')::uuid,
 CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END, to_jsonb(NEW), NEW.cancellation_reason);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER lr_audit AFTER INSERT OR UPDATE ON consignments FOR EACH ROW EXECUTE FUNCTION gram_lr_audit();
REVOKE ALL ON FUNCTION gram_lr_identity_guard(), gram_lr_audit(), gram_lr_location_guard(), gram_lr_private_guard() FROM PUBLIC;
DO $$ DECLARE t text; r text; BEGIN
 FOREACH t IN ARRAY ARRAY['issuing_locations','lr_sequences','lr_requests','lr_imports','lr_audit_log'] LOOP
   EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
   EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
   FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
     IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN EXECUTE format('REVOKE ALL ON %I FROM %I',t,r); END IF;
   END LOOP;
 END LOOP;
 -- Shipment Data API writes must not bypass the trusted backend/actor context.
 FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
   IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
     EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON consignments FROM %I',r);
   END IF;
 END LOOP;
END $$;

-- TRUNCATE bypasses row triggers; block it explicitly even for the backend role.
CREATE TRIGGER lr_counter_truncate BEFORE TRUNCATE ON lr_sequences FOR EACH STATEMENT EXECUTE FUNCTION gram_lr_private_guard();
CREATE TRIGGER lr_audit_truncate BEFORE TRUNCATE ON lr_audit_log FOR EACH STATEMENT EXECUTE FUNCTION gram_lr_private_guard();
CREATE TRIGGER lr_shipment_truncate BEFORE TRUNCATE ON consignments FOR EACH STATEMENT EXECUTE FUNCTION gram_lr_private_guard();
CREATE FUNCTION gram_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit history is append-only'; END $$;
CREATE TRIGGER lr_existing_audit_immutable BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION gram_audit_immutable();
CREATE TRIGGER lr_existing_audit_truncate BEFORE TRUNCATE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION gram_audit_immutable();
REVOKE ALL ON FUNCTION gram_audit_immutable() FROM PUBLIC;
