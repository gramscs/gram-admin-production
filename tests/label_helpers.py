import json
from uuid import uuid4


def label_form(ids, **options):
    return {'consignment_ids': ids, 'job_id': str(uuid4()),
            'label_details': json.dumps([{'id': str(value), 'invoice_amount': '6000.00',
                'payment_mode': 'pre-paid', 'goods_description': 'Automotive components',
                'eway_bill_exists': False} for value in dict.fromkeys(ids)]), **options}
