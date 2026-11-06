CREATE OR REPLACE FUNCTION gram_lr_identity_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = __SCHEMA__, pg_temp AS $$
DECLARE loc issuing_locations%ROWTYPE; moment timestamptz; fy varchar(4); seq integer; who uuid; role_name text; active_user boolean;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Issued and historical shipments cannot be deleted; cancel instead'; END IF;
 who := nullif(current_setting('gram.actor_id', true), '')::uuid;
 SELECT role INTO role_name FROM admin_users WHERE id = who AND active AND supabase_user_id IS NOT NULL;
 active_user := role_name IS NOT NULL;
 IF NOT active_user THEN RAISE EXCEPTION 'Active authenticated user required'; END IF;
 IF TG_OP = 'INSERT' THEN
   IF role_name NOT IN ('administrator','operator') THEN RAISE EXCEPTION 'Authorized LR operator required'; END IF;
   IF NEW.identifier_value IS NOT NULL OR NEW.lr_sequence IS NOT NULL OR NEW.financial_year IS NOT NULL
      OR NEW.lr_issued_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by IS NOT NULL
      OR NEW.cancellation_reason IS NOT NULL OR NEW.lr_status <> 'legacy' OR NEW.identifier_type <> 'LRN'
   THEN RAISE EXCEPTION 'GRAM generates LR identity automatically'; END IF;
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
     IF NEW.lr_status <> 'cancelled' OR length(trim(coalesce(NEW.cancellation_reason,''))) NOT BETWEEN 1 AND 1000
     THEN RAISE EXCEPTION 'Cancellation reason required'; END IF;
     NEW.cancelled_at := clock_timestamp(); NEW.cancelled_by := who;
   ELSIF (NEW.cancelled_at,NEW.cancelled_by,NEW.cancellation_reason) IS DISTINCT FROM (OLD.cancelled_at,OLD.cancelled_by,OLD.cancellation_reason) THEN
     RAISE EXCEPTION 'Use the cancellation workflow';
   END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION gram_lr_identity_guard() FROM PUBLIC;
