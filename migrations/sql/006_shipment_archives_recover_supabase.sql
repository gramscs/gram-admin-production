-- Recovery for an existing compatible archive table, not a fresh install.
-- Run ENTIRE file after review. No shipment/archive rows are deleted or updated.
BEGIN;
SET LOCAL search_path="public";
SET LOCAL lock_timeout='15s';
SET LOCAL statement_timeout='120s';
SELECT pg_advisory_xact_lock(723910042006);
DO $archive_recovery$
DECLARE previous text; sid smallint; aid smallint; ts smallint; payload smallint;
 item record; routine oid; existing_trigger record; before_count bigint; index_oid oid;
BEGIN
 IF to_regclass('admin_migration_runs') IS NULL OR to_regclass('shipment_archives') IS NULL THEN
   RAISE EXCEPTION 'Existing archive table and migration ledger required; use normal 006 for a fresh installation';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM admin_migration_runs WHERE version='005_centralized_lr') THEN
   RAISE EXCEPTION 'Apply and verify migration 005 first';
 END IF;
 SELECT target_digest INTO previous FROM admin_migration_runs WHERE version='006_shipment_archives';
 IF FOUND AND previous IS DISTINCT FROM '671c741f281e7aa6aca7997d571d44984b72331fc66a88380e60089b9653389f' THEN
   RAISE EXCEPTION 'Recorded 006 checksum differs; do not overwrite migration history';
 END IF;
 LOCK TABLE consignments IN SHARE ROW EXCLUSIVE MODE;
 LOCK TABLE shipment_archives IN ACCESS EXCLUSIVE MODE;
 IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid='shipment_archives'::regclass AND relkind='r')
 OR (SELECT count(*) FROM pg_attribute WHERE attrelid='shipment_archives'::regclass AND attnum>0 AND NOT attisdropped) <> 4
 THEN RAISE EXCEPTION 'Archive table structure differs; no changes made'; END IF;
 FOR item IN SELECT * FROM (VALUES
  ('shipment_id','uuid'::regtype), ('archived_by','uuid'::regtype),
  ('archived_at','timestamptz'::regtype), ('snapshot','jsonb'::regtype)) AS expected(name,kind) LOOP
   IF NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='shipment_archives'::regclass
     AND attname=item.name AND atttypid=item.kind AND attnotnull AND NOT attisdropped
     AND attgenerated='' AND attidentity='')
   THEN RAISE EXCEPTION 'Archive column % differs; no changes made',item.name; END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
   WHERE d.adrelid='shipment_archives'::regclass AND a.attname='archived_at' AND pg_get_expr(d.adbin,d.adrelid)='clock_timestamp()')
 OR EXISTS(SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
   WHERE d.adrelid='shipment_archives'::regclass AND a.attname<>'archived_at')
 THEN RAISE EXCEPTION 'Archive defaults differ; no changes made'; END IF;
 SELECT attnum INTO sid FROM pg_attribute WHERE attrelid='shipment_archives'::regclass AND attname='shipment_id';
 SELECT attnum INTO aid FROM pg_attribute WHERE attrelid='shipment_archives'::regclass AND attname='archived_by';
 SELECT attnum INTO ts FROM pg_attribute WHERE attrelid='shipment_archives'::regclass AND attname='archived_at';
 SELECT attnum INTO payload FROM pg_attribute WHERE attrelid='shipment_archives'::regclass AND attname='snapshot';
 IF (SELECT count(*) FROM pg_constraint WHERE conrelid='shipment_archives'::regclass)<>4
 OR EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='shipment_archives'::regclass AND (NOT convalidated OR condeferrable))
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='shipment_archives'::regclass AND contype='p' AND conkey=ARRAY[sid])
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='shipment_archives'::regclass AND contype='f' AND conkey=ARRAY[sid]
    AND confrelid='consignments'::regclass AND confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='consignments'::regclass AND attname='id')]
    AND confdeltype='r' AND confupdtype='a')
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='shipment_archives'::regclass AND contype='f' AND conkey=ARRAY[aid]
    AND confrelid='admin_users'::regclass AND confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='admin_users'::regclass AND attname='id')]
    AND confdeltype='r' AND confupdtype='a')
 OR NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='shipment_archives'::regclass AND contype='c'
    AND regexp_replace(pg_get_constraintdef(oid),'[[:space:]]','','g')='CHECK((jsonb_typeof(snapshot)=''object''::text))')
 THEN RAISE EXCEPTION 'Archive constraints differ; no changes made'; END IF;
 IF EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='shipment_archives'::regclass)
 THEN RAISE EXCEPTION 'Unexpected archive RLS policies; review access before recovery'; END IF;
 IF EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='shipment_archives'::regclass AND NOT tgisinternal
   AND tgname NOT IN ('archive_guard','archive_truncate_guard'))
 THEN RAISE EXCEPTION 'Unexpected archive triggers; review before recovery'; END IF;
 SELECT count(*) INTO before_count FROM shipment_archives;

 SELECT p.oid INTO routine FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='gram_archive_guard' AND p.pronargs=0;
 IF FOUND AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
   WHERE p.oid=routine AND p.prorettype='trigger'::regtype AND p.prosecdef
   AND l.lanname='plpgsql' AND btrim(p.prosrc)=btrim('
DECLARE who uuid; shipment uuid;
BEGIN
 who := nullif(current_setting(''gram.actor_id'',true),'''')::uuid;
 IF NOT EXISTS(SELECT 1 FROM admin_users WHERE id=who AND role=''administrator'' AND active AND supabase_user_id IS NOT NULL)
 THEN RAISE EXCEPTION ''Active administrator required to archive or restore''; END IF;
 IF TG_OP NOT IN (''INSERT'',''DELETE'') THEN RAISE EXCEPTION ''Archive snapshots are immutable''; END IF;
 shipment := CASE WHEN TG_OP=''DELETE'' THEN OLD.shipment_id ELSE NEW.shipment_id END;
 PERFORM id FROM consignments WHERE id=shipment FOR UPDATE;
 IF TG_OP=''DELETE'' THEN RETURN OLD; END IF;
 NEW.archived_by := who; NEW.archived_at := clock_timestamp();
 RETURN NEW;
END '))
 THEN RAISE EXCEPTION 'Conflicting archive function gram_archive_guard; recovery stopped without changing records'; END IF;
 EXECUTE $recover_function$CREATE OR REPLACE FUNCTION gram_archive_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = "public", pg_temp AS $$
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
END $$;$recover_function$;

 SELECT p.oid INTO routine FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='gram_archived_shipment_readonly' AND p.pronargs=0;
 IF FOUND AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
   WHERE p.oid=routine AND p.prorettype='trigger'::regtype AND p.prosecdef
   AND l.lanname='plpgsql' AND btrim(p.prosrc)=btrim('
BEGIN
 IF EXISTS(SELECT 1 FROM shipment_archives WHERE shipment_id=OLD.id)
 THEN RAISE EXCEPTION ''Restore archived shipment before changing it''; END IF;
 RETURN NEW;
END '))
 THEN RAISE EXCEPTION 'Conflicting archive function gram_archived_shipment_readonly; recovery stopped without changing records'; END IF;
 EXECUTE $recover_function$CREATE OR REPLACE FUNCTION gram_archived_shipment_readonly() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = "public", pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM shipment_archives WHERE shipment_id=OLD.id)
 THEN RAISE EXCEPTION 'Restore archived shipment before changing it'; END IF;
 RETURN NEW;
END $$;$recover_function$;

 FOR item IN SELECT * FROM (VALUES
   ('shipment_archives','archive_guard','gram_archive_guard',31),
   ('shipment_archives','archive_truncate_guard','gram_archive_guard',34),
   ('consignments','archived_shipment_readonly','gram_archived_shipment_readonly',19)) AS expected(tbl,trg,fn,kind) LOOP
   SELECT * INTO existing_trigger FROM pg_trigger WHERE tgrelid=to_regclass(item.tbl) AND tgname=item.trg;
   routine:=to_regprocedure(format('%I.%I()',current_schema(),item.fn));
   IF FOUND THEN
     IF existing_trigger.tgtype<>item.kind OR existing_trigger.tgfoid<>routine
       OR existing_trigger.tgqual IS NOT NULL OR existing_trigger.tgattr::text<>'' OR existing_trigger.tgnargs<>0
       OR existing_trigger.tgisinternal OR existing_trigger.tgconstraint<>0
     THEN RAISE EXCEPTION 'Conflicting trigger %; no changes made',item.trg; END IF;
     EXECUTE format('ALTER TABLE %I ENABLE TRIGGER %I',item.tbl,item.trg);
   ELSIF item.trg='archive_guard' THEN
     CREATE TRIGGER archive_guard BEFORE INSERT OR UPDATE OR DELETE ON shipment_archives FOR EACH ROW EXECUTE FUNCTION gram_archive_guard();
   ELSIF item.trg='archive_truncate_guard' THEN
     CREATE TRIGGER archive_truncate_guard BEFORE TRUNCATE ON shipment_archives FOR EACH STATEMENT EXECUTE FUNCTION gram_archive_guard();
   ELSE
     CREATE TRIGGER archived_shipment_readonly BEFORE UPDATE ON consignments FOR EACH ROW EXECUTE FUNCTION gram_archived_shipment_readonly();
   END IF;
 END LOOP;
 index_oid:=to_regclass('ix_shipment_archives_archived_at');
 IF index_oid IS NULL THEN CREATE INDEX ix_shipment_archives_archived_at ON shipment_archives(archived_at);
 ELSIF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
   WHERE i.indexrelid=index_oid AND i.indrelid='shipment_archives'::regclass AND i.indnatts=1
   AND i.indkey::text=ts::text AND i.indisvalid AND i.indisready AND NOT i.indisunique
   AND i.indpred IS NULL AND i.indexprs IS NULL AND a.amname='btree')
 THEN RAISE EXCEPTION 'Archive timestamp index differs; no changes made'; END IF;
 ALTER TABLE shipment_archives ENABLE ROW LEVEL SECURITY;
 REVOKE ALL ON shipment_archives FROM PUBLIC;
 REVOKE ALL ON FUNCTION gram_archive_guard(),gram_archived_shipment_readonly() FROM PUBLIC;
 FOR item IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') LOOP
   EXECUTE format('REVOKE ALL ON shipment_archives FROM %I',item.rolname);
 END LOOP;
 IF (SELECT count(*) FROM shipment_archives)<>before_count THEN RAISE EXCEPTION 'Archive preservation verification failed'; END IF;
 IF previous IS NULL THEN
   INSERT INTO admin_migration_runs(version,namespace,source_digest,target_digest,source_schema,counts,warnings,completed_at)
   VALUES('006_shipment_archives','bb195093-5195-5cbe-91a1-db8025a773af'::uuid,
      '671c741f281e7aa6aca7997d571d44984b72331fc66a88380e60089b9653389f','671c741f281e7aa6aca7997d571d44984b72331fc66a88380e60089b9653389f',
      '{"upgrade":"shipment_archives","recovered_existing_table":true}'::jsonb,
      jsonb_build_object('shipment_archives',jsonb_build_object('preserved_rows',before_count)),
      '[]'::jsonb,clock_timestamp());
 END IF;
END $archive_recovery$;
COMMIT;
SELECT version,target_digest,completed_at FROM "public".admin_migration_runs WHERE version='006_shipment_archives';
