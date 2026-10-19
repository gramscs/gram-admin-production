"""Coordinate explicit document deletion with private storage and SQL rollback."""
from contextlib import ExitStack
import hashlib
import mimetypes
import shutil
from tempfile import SpooledTemporaryFile

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import and_, or_

from app import orm as models
from app.models import db


class StorageSyncError(RuntimeError):
    """A safe message that can be shown without exposing provider credentials."""


def storage_object_missing(error):
    if isinstance(error, FileNotFoundError):
        return True
    # Supabase uses HTTP 400 as well as 404 for missing objects. Authorization,
    # connection and missing-bucket errors must never count as successful removal.
    try:
        from storage3.exceptions import StorageApiError
    except ImportError:
        return False
    if not isinstance(error, StorageApiError):
        return False
    code, message = str(error.code).lower(), str(error.message).lower()
    return ('bucket' not in message and code != 'nosuchbucket'
            and (code in ('not_found', 'nosuchkey') or message == 'object not found'))


def _managed_reference(value):
    return isinstance(value, str) and value.startswith('supabase:')


def document_is_retained(value):
    """Protect shared attachments, retained versions and report files."""
    canonical = value
    if models.using_er():
        from app.er_adapter import File
        from app.admin.consignment_controller import _parse_supabase_pod_value
        if not _managed_reference(canonical):
            return False
        bucket, path = _parse_supabase_pod_value(canonical)
        return db.session.query(File).filter(or_(File.storage_path == canonical,
            and_(File.storage_bucket == bucket, File.storage_path == path))).first() is not None
    return (models.Consignment.query.filter(or_(models.Consignment.pod_image == canonical,
                                              models.Consignment.invoice_file == canonical,
                                              models.Consignment.lr_file == canonical)).first() is not None
            or models.MisReport.query.filter(models.MisReport.file_ref == canonical).first() is not None)


def purge_shipment_documents(row, obsolete, kind=None):
    """Explicit removal purges the selected kind's entire version history."""
    from app.admin.documents import DOCUMENTS
    kinds = [kind] if kind else list(DOCUMENTS)
    if not models.using_er():
        for item in kinds:
            field, name_field = DOCUMENTS[item]
            if value := getattr(row, field):
                obsolete.append(value)
            setattr(row, field, None)
            setattr(row, name_field, None)
        return
    from app.er_adapter import File, MisReport, ShipmentDocument
    from app.admin.documents import DOCUMENT_TYPES
    types = {DOCUMENT_TYPES[item] for item in kinds}
    documents = [doc for doc in row.documents if doc.document_type in types]
    file_ids = {doc.file_id for doc in documents}
    for doc in documents:
        db.session.delete(doc)
    if not documents:
        return
    db.session.flush()
    # Reload the collection so the adapter cannot return the just-deleted current
    # document; don't null its non-nullable consignment foreign key.
    db.session.expire(row, ['documents'])
    for file in db.session.query(File).filter(File.id.in_(file_ids)).order_by(File.id).with_for_update().all():
        if (not db.session.query(ShipmentDocument).filter_by(file_id=file.id).first()
                and not db.session.query(MisReport).filter_by(file_id=file.id).first()):
            from app.admin.documents import storage_reference
            obsolete.append(storage_reference(file.storage_bucket, file.storage_path))
            db.session.delete(file)


def _restore_document(value, backup):
    from app.admin.consignment_controller import _get_supabase_client, _parse_supabase_pod_value
    from app.admin.documents import open_stored_document
    backup.seek(0)
    digest = hashlib.file_digest(backup, 'sha256').digest()
    try:
        with open_stored_document(value) as (source, _):
            if hashlib.file_digest(source, 'sha256').digest() != digest:
                raise RuntimeError('Stored document contents changed during rollback.')
            return  # A failed delete may have left the original object untouched.
    except Exception as error:
        if not storage_object_missing(error):
            raise
    backup.seek(0)
    if value.startswith('supabase:'):
        bucket, path = _parse_supabase_pod_value(value)
        client = _get_supabase_client()
        if client is None:
            raise RuntimeError('Supabase is not configured.')
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        client.storage.from_(bucket).upload(path, backup.read(), {'content-type': mime, 'upsert': 'true'})
    else:
        raise RuntimeError('Cannot restore a document that is not stored in Supabase.')
    with open_stored_document(value) as (source, _):
        if hashlib.file_digest(source, 'sha256').digest() != digest:
            raise RuntimeError('Storage did not restore the original document.')


def commit_document_cleanup(obsolete):
    """Delete storage before SQL commit, compensating on failure.

    SQL and object storage cannot share a transaction. Temporary copies restore
    objects if a later deletion or SQL commit fails; no success is reported until
    the provider confirms deletion. A process crash remains a cross-system risk.
    """
    from app.admin.consignment_controller import _delete_pod_file
    from app.admin.documents import open_stored_document
    with ExitStack() as stack:
        backups, attempted = {}, []
        try:
            db.session.flush()
            for value in sorted(set(filter(None, obsolete))):
                if not _managed_reference(value) or document_is_retained(value):
                    continue
                backup = stack.enter_context(SpooledTemporaryFile(max_size=5 * 1024 * 1024))
                try:
                    with open_stored_document(value) as (source, _):
                        shutil.copyfileobj(source, backup)
                except Exception as error:
                    if storage_object_missing(error):
                        continue  # Already absent: finish removing its stale SQL metadata.
                    raise
                backups[value] = backup
            for value in backups:
                attempted.append(value)
                _delete_pod_file(value)
            db.session.commit()
        except Exception as error:
            current_app.logger.error('Document cleanup or database commit failed (%s).', type(error).__name__)
            db.session.rollback()
            restoration_failed = False
            for value in reversed(attempted):
                try:
                    _restore_document(value, backups[value])
                except Exception:
                    restoration_failed = True
                    current_app.logger.error('Document storage restoration failed; manual recovery is required.')
            if restoration_failed:
                raise StorageSyncError('Save failed and a stored file could not be restored. Database changes were rolled back. Check storage before retrying.') from None
            raise StorageSyncError('Save failed. Your previous documents were preserved. Check database and Storage delete permissions, then try again.') from None


@click.command('purge-removed-documents')
@click.option('--identifier', required=True, help='Exact shipment identifier, for example DEMO0017.')
@click.option('--kind', type=click.Choice(['pod', 'invoice', 'lr']), required=True)
@click.option('--apply', is_flag=True, help='Delete the already-removed document versions and unshared files.')
@with_appcontext
def purge_removed_documents(identifier, kind, apply):
    """Clean up versions left by the old removal behavior; dry run by default."""
    from app.admin.documents import DOCUMENTS
    if not models.using_er():
        raise click.ClickException('This repair requires the ER database, which records old document versions.')
    row = models.shipment_query().filter_by(consignment_number=identifier.strip().upper()).with_for_update().first()
    if not row:
        raise click.ClickException('Shipment not found. No files were changed.')
    if getattr(row, DOCUMENTS[kind][0]):
        raise click.ClickException('This shipment has a current document of that type. Remove it in the portal first. No files were changed.')
    documents = [doc for doc in row.documents if doc.document_type == {'pod': 'POD', 'invoice': 'invoice', 'lr': 'LR'}[kind]]
    click.echo(f'{row.consignment_number}: {len(documents)} already-removed {kind.upper()} version(s).')
    if not apply:
        db.session.rollback()
        click.echo('Dry run. No files or database records were changed. Add --apply to delete these versions; shared files will be kept.')
        return
    obsolete = []
    try:
        purge_shipment_documents(row, obsolete, kind)
        commit_document_cleanup(obsolete)
    except StorageSyncError as error:
        raise click.ClickException(str(error)) from None
    except Exception:
        db.session.rollback()
        raise click.ClickException('Cleanup failed. Check the database and storage. No success was recorded.') from None
    click.echo(f'Removed {len(documents)} {kind.upper()} version(s) and {len(set(obsolete))} unshared file reference(s). Other shipment data was preserved.')
