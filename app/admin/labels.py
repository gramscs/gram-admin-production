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


def _fit_text(pdf, text, x, y, width, height, font='LabelSans', size=10, leading=None, alignment=0, text_color=colors.black):
    paragraph = Paragraph(text, ParagraphStyle('label', fontName=font, fontSize=size, leading=leading or size*1.2, textColor=text_color, alignment=alignment))
    box = KeepInFrame(width, height, [paragraph], mode='shrink')
    _, used_height = box.wrapOn(pdf, width, height)
    box.drawOn(pdf, x, y-used_height)


def _address(pdf, title, name, address, pin, top, height, recipient=False):
    left, width = 18, 252
    pdf.setFont('LabelSansBold', 6.5)
    pdf.drawString(left, top, title)
    if not recipient:
        pdf.setFont('LabelSansBold', 7)
        pdf.drawRightString(left + width, top, 'PIN: ' + (pin or '—'))
    text = '<b>' + escape(name or ('Recipient' if recipient else 'Sender')) + '</b><br/>' + escape(address or 'Address not provided').replace('\n', '<br/>')
    _fit_text(pdf, text, left, top-7, width, height, size=11 if recipient else 8.5, leading=13 if recipient else 10.5)


def generate_labels_pdf(shipments, assignments, include_barcode=True):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(4*inch, 6*inch))
    pdf.setTitle('Gram SCS B2B Shipping Labels')
    pdf.setAuthor('Gram SCS')
    logo = Path(current_app.static_folder, 'images', 'logo.png')
    for shipment, assignment in zip(shipments, assignments, strict=True):
        pieces = shipment['pieces']
        for piece in range(1, pieces+1):
            # An inset double frame keeps the border clear of all printed data.
            pdf.saveState()
            pdf.setStrokeColor(colors.HexColor('#365A3A'))
            pdf.setLineWidth(.9)
            pdf.roundRect(8, 6, 272, 420, 8, fill=0, stroke=1)
            pdf.setStrokeColor(colors.HexColor('#BCCDB2'))
            pdf.setLineWidth(.35)
            pdf.roundRect(11, 9, 266, 414, 6, fill=0, stroke=1)
            pdf.restoreState()
            pdf.setStrokeColor(colors.black)
            pdf.setFillColor(colors.black)
            # The reference's compact header uses our logo and real payment mode.
            pdf.drawImage(str(logo), 18, 391, width=63, height=27, mask='auto')
            pdf.rect(164, 398, 106, 18, fill=1, stroke=0)
            _fit_text(pdf, PAYMENT_MODES[shipment['payment_mode']].upper(), 169, 413, 96, 12,
                      font='LabelSansBold', size=7.5, alignment=1, text_color=colors.white)
            _fit_text(pdf, escape(shipment['company']), 18, 385, 252, 8, size=6.5)
            pdf.setLineWidth(1.8)
            pdf.line(18, 376, 270, 376)
            pdf.setLineWidth(.5)

            # Sender, destination and routing PIN follow the reference hierarchy.
            _address(pdf, 'SHIP FROM', shipment['pickup_tag'], shipment['pickup_address'], shipment['pickup_pincode'], 366, 32)
            pdf.line(18, 318, 270, 318)
            _address(pdf, 'DELIVER TO', shipment['drop_tag'], shipment['drop_address'], shipment['drop_pincode'], 307, 54, recipient=True)
            pdf.rect(18, 208, 252, 32, fill=1, stroke=0)
            _fit_text(pdf, escape(shipment['drop_pincode'] or 'PIN NOT SET'), 26, 238, 236, 20,
                      font='LabelSansBold', size=18, alignment=1, text_color=colors.white)
            pdf.setFillColor(colors.white)
            pdf.setFont('LabelSans', 5.5)
            pdf.drawCentredString(144, 213, 'DESTINATION PIN')
            pdf.setFillColor(colors.black)

            # Measurements, boxes, pickup date and piece count share one row.
            pdf.setFont('LabelSans', 6)
            pdf.drawString(18, 198, 'CHARGEABLE TOTALS')
            pdf.drawString(101, 198, 'BOXES')
            pdf.drawString(149, 198, 'PICKUP DATE')
            pdf.drawString(237, 198, 'PIECE')
            weight, volume = shipment['chargeable_weight'], shipment['chargeable_volume']
            _fit_text(pdf, (weight or '—') + ' kg', 18, 194, 75, 12, font='LabelSansBold', size=9)
            if volume is not None:
                _fit_text(pdf, volume + ' m3', 18, 182, 75, 9, size=7)
            _fit_text(pdf, str(pieces), 101, 194, 40, 12, font='LabelSansBold', size=9)
            _fit_text(pdf, escape(shipment['pickup_date']), 149, 194, 80, 12, font='LabelSansBold', size=8)
            _fit_text(pdf, f'{piece} of {pieces}', 237, 194, 33, 12, font='LabelSansBold', size=8)
            pdf.setLineWidth(1)
            pdf.line(18, 166, 270, 166)
            pdf.setLineWidth(.5)

            # Human-readable tracking remains when the optional barcode is off.
            pdf.setFont('LabelSans', 6)
            pdf.drawCentredString(144, 155, 'TRACKING NUMBER')
            if include_barcode:
                barcode = code128.Code128(shipment['consignment_number'], barHeight=30, barWidth=.8, humanReadable=False)
                pdf.saveState()
                scale = min(1.8, 252 / barcode.width)
                pdf.translate(18 + (252 - barcode.width * scale) / 2, 115)
                pdf.scale(scale, 1)
                barcode.drawOn(pdf, 0, 0)
                pdf.restoreState()
            _fit_text(pdf, escape(shipment['consignment_number']), 18, 109 if include_barcode else 135,
                      252, 19 if include_barcode else 32, font='LabelSansBold',
                      size=14 if include_barcode else 18, leading=16 if include_barcode else 20, alignment=1)
            pdf.line(18, 86, 270, 86)

            # Existing commercial parameters stay in a compact, aligned footer.
            _fit_text(pdf, '<b>INVOICE AMOUNT</b>  Rs ' + f"{Decimal(shipment['invoice_amount']):,.2f}", 18, 82, 252, 12, size=9)
            pdf.setFont('LabelSans', 6)
            pdf.drawString(18, 64, 'ORDER NUMBER')
            pdf.drawString(148, 64, 'INVOICE NUMBER')
            _fit_text(pdf, assignment['order_id'], 18, 60, 122, 10, size=7.5)
            _fit_text(pdf, assignment['invoice_number'], 148, 60, 122, 10, size=7.5)
            pdf.drawString(18, 43, 'GOODS DESCRIPTION')
            _fit_text(pdf, escape(shipment['goods_description']).replace('\n', '<br/>'), 18, 39, 252, 20, size=7.5, leading=9)
            if shipment['eway_bill_exists']:
                pdf.setFont('LabelSansBold', 7)
                pdf.drawString(18, 12, 'E-WAY BILL: ' + shipment['eway_bill_number'])
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
