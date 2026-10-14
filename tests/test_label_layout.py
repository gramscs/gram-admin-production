"""Printing layout checks: content-driven addresses, safe blocks and embedded fonts."""
import io
import shutil
import subprocess
from xml.etree import ElementTree

import pytest
from pypdf import PdfReader, PdfWriter

from app.admin.labels import generate_labels_pdf


def shipment():
    return dict(id='example', pieces=1, company='Sample client', invoice_amount='6000.00',
        measurement_mode='weight', weight='100.000', volume='1.500',
        pickup_date='09/10/2026', consignment_number='320986788', sender_name='Dispatch Team',
        receiver_name='Receiving Team', pickup_address='429 Jagraj Gram, Delhi 110001',
        pickup_pincode='110001', drop_address='24 Distribution Road, Delhi 110037',
        drop_pincode='110037', payment_mode='pre-paid', goods_description='Automotive spare parts',
        eway_bill_exists=True, eway_bill_number='123456789012')


def render(app, row, barcode=True):
    assignment = {'order_id': 'B2B-20261011-0001', 'invoice_number': 'B2B-2026-27-0001'}
    with app.app_context():
        return PdfReader(generate_labels_pdf([row], [assignment], barcode)).pages[0]


def positioned_text(page):
    # Poppler gives actual glyph bounds; pypdf's visitor misreports multiline
    # TrueType leading, so use it only for text/content checks below.
    if not shutil.which('pdftotext'):
        pytest.skip('PDF geometry checks require optional Poppler pdftotext.')
    writer = PdfWriter(); writer.add_page(page)
    content = io.BytesIO(); writer.write(content)
    result = subprocess.run(['pdftotext', '-bbox-layout', '-', '-'], input=content.getvalue(), capture_output=True, check=True)
    xml = ElementTree.fromstring(result.stdout)
    ns = '{http://www.w3.org/1999/xhtml}'
    return [(' '.join(word.text or '' for word in line.iter(ns + 'word')),
             float(line.get('xMin')), 432-float(line.get('yMax')))
            for line in xml.iter(ns + 'line')]


def baseline(items, text):
    return next(y for value, x, y in items if text in value)


def test_blank_lines_indents_and_spaces_do_not_change_address_layout(app):
    clean = shipment()
    padded = {**clean, 'pickup_address': '\r\n\r\n  429   Jagraj Gram, Delhi 110001  \r\n\r\n',
        'drop_address': '\n\n\t 24 Distribution Road,  Delhi 110037\n\n'}
    first, second = render(app, clean), render(app, padded)
    assert positioned_text(first) == positioned_text(second)
    text = first.extract_text()
    assert text.count('110001') == text.count('110037') == 1
    items = positioned_text(first)
    assert 0 < baseline(items, 'SHIP FROM') - baseline(items, 'Dispatch Team') <= 13
    assert 0 < baseline(items, 'DELIVER TO') - baseline(items, 'Receiving Team') <= 14
    # Short addresses no longer leave the recipient/metrics at fixed lower rows.
    assert 0 < baseline(items, '110037') - baseline(items, 'WEIGHT') < 22


@pytest.mark.parametrize('retired_overrides', [False, True])
@pytest.mark.parametrize('barcode', [True, False])
@pytest.mark.parametrize('long_address', [False, True])
@pytest.mark.parametrize('eway', [False, True])
def test_addresses_references_and_goods_stay_inside_distinct_print_blocks(app, barcode, long_address, eway, retired_overrides):
    row = shipment()
    row.update(eway_bill_exists=eway, goods_description='Sensitive precision components in reinforced protective cartons; keep dry and handle carefully. ' + 'Check all pieces before accepting delivery.')
    if retired_overrides:
        row['package_contents'] = {'kind': 'box', 'default_pieces': 24, 'boxes': {'1': {'pieces': 12, 'note': 'Fragile components — handle upright and keep dry. Confirm the carton seal and count before accepting this special package.'}}}
    if long_address:
        row.update(sender_name='Dispatch team ' + 'industrial logistics company '*3,
            receiver_name='Receiving team ' + 'distribution services company '*3,
            pickup_address='\n\n'.join(['Unit 1001, Industrial Logistics Estate, Loading Bay D',
                'Main Road near the distribution park', 'Commercial District, New Delhi 110001']),
            drop_address='\n\n'.join(['Unit 802, Distribution Warehouse, Delivery Gate F',
                'Industrial Logistics Estate, Building Alpha', 'Main Road near the commercial complex',
                'Bandra West, Mumbai', 'Maharashtra 110037']))
    page = render(app, row, barcode)
    items = positioned_text(page)
    for value, x, y in items:
        assert 18 <= x <= 270, (value, x, y)
        assert 16 <= y <= 418, (value, x, y)
    assert baseline(items, '110037') > baseline(items, 'WEIGHT') > baseline(items, 'TRACKING NUMBER') > baseline(items, row['consignment_number']) > 128
    assert baseline(items, 'Rs 6,000.00') < 116
    assert baseline(items, 'B2B-20261011-0001') == baseline(items, 'B2B-2026-27-0001')
    text = page.extract_text()
    assert ('EWBN:' in text) == eway
    for part in ('Unit 802', 'Loading Bay D', 'Maharashtra 110037') if long_address else ('429 Jagraj Gram', '24 Distribution Road'):
        assert part in text
    assert 'accepting delivery.' in text
    if retired_overrides:
        assert '\n24\n' in text
        assert 'BOX NOTE' not in text and 'this special package.' not in text
    fonts = [font.get_object() for font in page['/Resources']['/Font'].values()]
    mono = [font for font in fonts if 'LiberationMono' in font.get('/BaseFont', '')]
    assert len(mono) == 2
    assert all('/FontFile2' in font['/FontDescriptor'].get_object() for font in mono)
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (288, 432)
