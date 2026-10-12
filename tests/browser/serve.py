"""Disposable, migrated ER app for browser tests; never uses the real database."""
import os
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    with tempfile.TemporaryDirectory(prefix='gram-admin-browser-') as temporary:
        root = Path(temporary)
        os.environ.update(
            DATABASE_URL=f'sqlite:///{root}/admin.db', FLASK_ENV='development',
            ADMIN_USERNAME='admin', ADMIN_PASSWORD='system-test-only',
            ADMIN_PASSWORD_HASH=generate_password_hash('system-test-only'),
            SUPABASE_URL='', SUPABASE_KEY='', SUPABASE_MIS_BUCKET='',
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app import create_app
        from app.models import Company, Consignment, Lead, NewsletterSubscriber, db
        from app.er_adapter import prepare_backend
        from migrations.shipment_v1 import migrate
        from migrations.storage import Storage

        source = create_engine(f'sqlite:///{root}/source.db')
        target = create_engine(os.environ['DATABASE_URL'])
        db.metadata.create_all(source)
        with Session(source) as session:
            session.add(Company(id=1, name='System Test Client', address='Mumbai office'))
            session.add_all(Consignment(
                consignment_number=f'SYS-{index:03}', company_id=1,
                identifier_type='LRN', pieces=2, chargeable_weight=10,
                status=['In Transit', 'Delivered', 'Pickup Scheduled'][index % 3],
                pickup_address='Delhi warehouse', drop_address='Mumbai store',
                pickup_date='2026-10-01', pickup_pincode='110001', drop_pincode='400001',
            ) for index in range(60))
            session.add(Lead(name='Archived enquiry', email='test@example.com', message='Original enquiry'))
            session.add(NewsletterSubscriber(email='subscriber@example.com'))
            session.commit()
        (root / 'uploads').mkdir()
        (root / 'uploads' / 'unlinked.txt').write_text('Unlinked original upload')
        try:
            migrate(target, Storage(root / 'uploads'), source_engine=source, fresh_target=True)
            prepare_backend(target)
        finally:
            source.dispose()
            target.dispose()
        application = create_app({
            'INSTANCE_PATH': str(root), 'AUTO_CREATE_TABLES': False,
            'ADMIN_DATABASE_MODEL': 'auto', 'SECRET_KEY': 'isolated-browser-test-key',
            'RATELIMIT_ENABLED': False,
        })
        print('Disposable browser app: 60 test shipments; local uploads only.', flush=True)
        try:
            application.run(host='127.0.0.1', port=int(os.getenv('SYSTEM_TEST_PORT', '10003')), debug=False)
        finally:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    main()
