"""Print-friendly 4 x 6 inch B2B labels with optional Code 128 identifiers."""

import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import reportlab
from flask import current_app, jsonify, render_template, request, send_file, session
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepInFrame, Paragraph
from sqlalchemy import or_

from app.admin import admin_bp
from app.admin.auth import require_admin
from app import orm as models
from app.admin.label_runs import LabelRunConflict, render_label_run
from app.admin.reporting import parse_date

PAYMENT_MODES = {'pre-paid': 'Pre-paid', 'to-pay': 'To pay', 'cash-on-delivery': 'Cash on delivery'}


def label_shipment(row):
    day = parse_date(row.pickup_date)
    return {'id': str(row.id), 'consignment_number': row.consignment_number,
            'identifier_type': row.identifier_type or 'LRN', 'pieces': row.pieces or 1,
            'company': row.company.name if row.company else '',
            'pickup_date': day.strftime('%d/%m/%Y') if day else row.pickup_date or 'Not set',
            'pickup_address': row.pickup_address or '', 'pickup_tag': row.pickup_tag or '',
            'pickup_pincode': row.pickup_pincode or '', 'drop_address': row.drop_address or '',
            'drop_tag': row.drop_tag or '', 'drop_pincode': row.drop_pincode or '',
            'measurement_mode': row.measurement_mode or 'weight',
            'chargeable_weight': str(row.chargeable_weight) if row.chargeable_weight is not None else None,
            'chargeable_volume': str(row.chargeable_volume) if row.chargeable_volume is not None else None}


def _selected_rows(values):
    if not isinstance(values, list) or not values or len(values) > 500:
        raise ValueError('Select between 1 and 500 saved shipments.')
    selected = list(dict.fromkeys(models.record_id(value) for value in values))
    found = {row.id: row for row in models.shipment_query().filter(models.Consignment.id.in_(selected)).all()}
    if len(found) != len(selected):
        raise ValueError('A selected shipment no longer exists. Refresh the list and select again.')
    rows = [found[row_id] for row_id in selected]
    if sum(row.pieces or 1 for row in rows) > 500:
        raise ValueError('Selected shipments exceed 500 piece labels. Select fewer shipments.')
    return rows


def _same_origin():
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise ValueError('Label requests must come from this admin app.')


def _review_details(values, rows):
    if not isinstance(values, list) or len(values) != len(rows):
        raise ValueError('Enter label details for every selected shipment.')
    details = {}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Invalid shipment label details.')
        identifier = str(models.record_id(item.get('id')))
        if identifier in details:
            raise ValueError('Label details include a duplicate shipment.')
        amount = item.get('invoice_amount')
        if isinstance(amount, bool) or not isinstance(amount, (str, int)) or len(str(amount)) > 20 or not str(amount).strip():
            raise ValueError('Enter an invoice amount for every shipment.')
        try:
            amount = Decimal(str(amount).strip())
            if not amount.is_finite() or amount < 0 or amount > Decimal('999999999.99') or amount != amount.quantize(Decimal('.01')):
                raise ValueError('Invoice amounts must be between Rs 0 and Rs 999,999,999.99, with at most two decimal places.')
        except InvalidOperation:
            raise ValueError('Enter a valid invoice amount.') from None
        payment = item.get('payment_mode')
        if payment not in PAYMENT_MODES:
            raise ValueError('Choose Pre-paid, To pay or Cash on delivery for every shipment.')
        description = item.get('goods_description')
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 160 or any(ord(char) < 32 and char not in '\n\t' for char in description):
            raise ValueError('Enter a goods description of 1–160 characters for every shipment.')
        exists = item.get('eway_bill_exists', False)
        if not isinstance(exists, bool):
            raise ValueError('E-way bill exists must be true or false.')
        eway = item.get('eway_bill_number', '') if exists else ''
        if exists and (not isinstance(eway, str) or not re.fullmatch(r'[0-9]{12}', eway.strip())):
            raise ValueError('Enter the 12-digit e-way bill number, or uncheck E-way bill exists.')
        details[identifier] = {'invoice_amount': f'{amount:.2f}', 'payment_mode': payment,
                               'goods_description': description.strip(), 'eway_bill_exists': exists,
                               'eway_bill_number': eway.strip()}
    if set(details) != {str(row.id) for row in rows}:
        raise ValueError('Label details must match exactly the selected shipments.')
    return [{**label_shipment(row), **details[str(row.id)]} for row in rows]


# Embed fonts supplied by ReportLab so printing does not depend on local fonts.
_font_dir = Path(reportlab.__file__).resolve().parent / 'fonts'
for _name, _filename in (('LabelSans', 'Vera.ttf'), ('LabelSansBold', 'VeraBd.ttf')):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, str(_font_dir / _filename)))
pdfmetrics.registerFontFamily('LabelSans', normal='LabelSans', bold='LabelSansBold', italic='LabelSans', boldItalic='LabelSansBold')


def _label_rows():
    page = max(1, request.args.get('page', 1, type=int))
    search = request.args.get('search', '').strip()
    query = models.shipment_query()
    if search:
        query = query.filter(or_(models.Consignment.consignment_number.ilike(f'%{search}%'), models.Consignment.company.has(models.Company.name.ilike(f'%{search}%')),
                                models.Consignment.pickup_tag.ilike(f'%{search}%'), models.Consignment.drop_tag.ilike(f'%{search}%')))
    total = query.count()
    rows = query.order_by(models.Consignment.id.desc()).offset((page - 1) * 25).limit(25).all()
    return dict(rows=rows, label_page_rows=[label_shipment(row) for row in rows], page=page, total=total, search=search)


@admin_bp.get('/admin/labels')
@require_admin
def labels_panel():
    return render_template('admin/labels.html', **_label_rows())


@admin_bp.post('/admin/labels/review')
@require_admin
def labels_review():
    try:
        _same_origin()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Send the selected shipment IDs.')
        rows = _selected_rows(data.get('consignment_ids'))
        return jsonify(success=True, shipments=[label_shipment(row) for row in rows])
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400


def _fit_text(pdf, text, x, y, width, height, font='LabelSans', size=10, leading=None):
    paragraph = Paragraph(text, ParagraphStyle('label', fontName=font, fontSize=size, leading=leading or size*1.2, textColor=colors.black))
    box = KeepInFrame(width, height, [paragraph], mode='shrink')
    _, used_height = box.wrapOn(pdf, width, height)
    box.drawOn(pdf, x, y-used_height)


def _address(pdf, title, name, address, pin, top, height, recipient=False):
    pdf.setFont('LabelSansBold', 8)
    pdf.drawString(22, top, title)
    pdf.setFont('LabelSansBold', 13 if recipient else 10)
    pdf.drawRightString(266, top, 'PIN: ' + (pin or '—'))
    text = '<b>' + escape(name or ('Recipient' if recipient else 'Sender')) + '</b><br/>' + escape(address or 'Address not provided').replace('\n', '<br/>')
    _fit_text(pdf, text, 22, top-13, 244, height, size=12 if recipient else 9, leading=15 if recipient else 11)


def generate_labels_pdf(shipments, assignments, include_barcode=True):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(4*inch, 6*inch))
    pdf.setTitle('Gram SCS B2B Shipping Labels')
    pdf.setAuthor('Gram SCS')
    logo = Path(current_app.static_folder, 'images', 'logo.png')
    for shipment, assignment in zip(shipments, assignments, strict=True):
        pieces = shipment['pieces']
        for piece in range(1, pieces+1):
            pdf.setStrokeColor(colors.HexColor('#444444'))
            pdf.setLineWidth(.6)
            pdf.roundRect(12, 12, 264, 408, 5)
            pdf.setFillColor(colors.black)
            pdf.setFont('LabelSansBold', 14)
            pdf.drawImage(str(logo), 22, 385, width=77, height=33, mask='auto')
            pdf.drawString(111, 403, 'GRAM SCS')
            pdf.setFont('LabelSansBold', 9)
            pdf.drawString(111, 389, 'GRAM B2B')
            _fit_text(pdf, escape(shipment['company']), 22, 378, 244, 10, size=7)
            pdf.line(22, 365, 266, 365)
            pdf.setFont('LabelSans', 6)
            pdf.drawString(22, 354, 'INVOICE AMOUNT')
            pdf.drawString(135, 354, 'BOXES')
            pdf.drawString(192, 354, 'CHARGEABLE TOTALS')
            _fit_text(pdf, 'Rs ' + f"{Decimal(shipment['invoice_amount']):,.2f}", 22, 348, 108, 18, font='LabelSansBold', size=12)
            pdf.setFont('LabelSansBold', 12)
            pdf.drawString(135, 335, str(pieces))
            weight, volume = shipment['chargeable_weight'], shipment['chargeable_volume']
            _fit_text(pdf, (weight or '—') + ' kg', 192, 348, 74, 12, font='LabelSansBold', size=9)
            if volume is not None:
                _fit_text(pdf, volume + ' m3', 192, 334, 74, 10, size=7)
            _fit_text(pdf, 'PICKUP DATE: ' + escape(shipment['pickup_date']), 22, 321, 168, 12, size=8)
            pdf.setFillColor(colors.HexColor('#eeeeee'))
            pdf.roundRect(192, 310, 74, 16, 4, fill=1, stroke=0)
            pdf.setFillColor(colors.black)
            pdf.setFont('LabelSansBold', 8)
            pdf.drawCentredString(229, 315, f'Piece {piece} of {pieces}')
            pdf.setFont('LabelSans', 7)
            pdf.drawString(22, 300, 'SHIPMENT / ' + shipment['identifier_type'])
            _fit_text(pdf, escape(shipment['consignment_number']), 22, 294, 244, 25, font='LabelSansBold', size=17, leading=18)
            if include_barcode:
                barcode = code128.Code128(shipment['consignment_number'], barHeight=25, barWidth=.8, humanReadable=False)
                pdf.saveState()
                pdf.translate(18, 246)
                pdf.scale(min(1, 252/barcode.width), 1)
                barcode.drawOn(pdf, 0, 0)
                pdf.restoreState()
            pdf.line(22, 237, 266, 237)
            _address(pdf, 'DELIVER TO', shipment['drop_tag'], shipment['drop_address'], shipment['drop_pincode'], 224, 52, recipient=True)
            pdf.line(22, 152, 266, 152)
            _address(pdf, 'SHIP FROM', shipment['pickup_tag'], shipment['pickup_address'], shipment['pickup_pincode'], 139, 35)
            pdf.line(22, 86, 266, 86)
            _fit_text(pdf, '<b>PAYMENT:</b> ' + PAYMENT_MODES[shipment['payment_mode']], 22, 80, 244, 11, size=8)
            _fit_text(pdf, '<b>ORDER ID:</b> ' + assignment['order_id'], 22, 67, 244, 10, size=8)
            _fit_text(pdf, '<b>INVOICE:</b> ' + assignment['invoice_number'], 22, 55, 244, 10, size=8)
            _fit_text(pdf, '<b>GOODS:</b> ' + escape(shipment['goods_description']).replace('\n', '<br/>'), 22, 43, 244, 20, size=8)
            if shipment['eway_bill_exists']:
                pdf.setFont('LabelSansBold', 7)
                pdf.drawString(22, 17, 'E-WAY BILL: ' + shipment['eway_bill_number'])
            pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


@admin_bp.post('/admin/labels/generate')
@require_admin
def labels_generate():
    try:
        _same_origin()
        data = request.get_json(silent=True) if request.is_json else request.form
        if data is None:
            raise ValueError('Send the reviewed label details.')
        ids = data.get('consignment_ids') if request.is_json else request.form.getlist('consignment_ids')
        rows = _selected_rows(ids)
        include_barcode = data.get('barcode', '1') not in ('0', False)
        if include_barcode and any(not row.consignment_number.isascii() or not all(32 <= ord(char) <= 126 for char in row.consignment_number) for row in rows):
            raise ValueError('Code 128 needs printable ASCII identifiers. Edit the identifier or print without a barcode.')
        details = data.get('label_details')
        if not request.is_json and isinstance(details, str):
            details = json.loads(details)
        shipments = _review_details(details, rows)
        output = render_label_run(data.get('job_id'), session.get('admin_username') or 'admin', shipments,
            lambda assignments: generate_labels_pdf(shipments, assignments, include_barcode))
    except LabelRunConflict as error:
        return jsonify(success=False, message=str(error)), 409
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400
    except Exception:
        current_app.logger.exception('Label generation failed')
        return jsonify(success=False, message='Labels could not be generated. Check the label numbering files and try again.'), 500
    response = send_file(output, as_attachment=data.get('delivery') != 'preview', download_name='b2b-shipping-labels.pdf', mimetype='application/pdf')
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
