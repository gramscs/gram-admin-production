"""Print-friendly 4 x 6 inch B2B labels with optional Code 128 identifiers."""

import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

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
from app.shipment_packages import volume_ft3, package_at
from app.admin.label_runs import LabelRunConflict, render_label_run
from app.admin.reporting import parse_date

PAYMENT_MODES = {'pre-paid': 'Pre-paid', 'to-pay': 'To pay', 'cash-on-delivery': 'Cash on delivery'}


def _label_record_id(value, *, details=False):
    try:
        return models.record_id(value)
    except ValueError:
        message = ('The label details are out of date. Reopen Generate labels and review the shipments again.'
                   if details else 'The saved shipment selection is out of date. Refresh Labels and select your shipments again.')
        raise ValueError(message) from None


def label_shipment(row, *, include_boxes=False):
    day = parse_date(row.pickup_date)
    shipment = {'id': str(row.id), 'consignment_number': row.consignment_number,
            'identifier_type': row.identifier_type or 'LRN', 'pieces': row.pieces or 1,
            'company': row.company.name if row.company else '',
            'pickup_date': day.strftime('%d/%m/%Y') if day else row.pickup_date or 'Not set',
            'pickup_address': row.pickup_address or '', 'pickup_tag': row.pickup_tag or '',
            'pickup_pincode': row.pickup_pincode or '', 'drop_address': row.drop_address or '',
            'drop_tag': row.drop_tag or '', 'drop_pincode': row.drop_pincode or '',
            'measurement_mode': row.measurement_mode or ('volume' if row.volume is not None and row.weight is None else 'weight'),
            'package_contents': row.package_contents, 'volume_unit': 'ft3',
            'weight': str(row.weight) if row.weight is not None else None,
            'volume': str(volume_ft3(row)) if row.volume is not None else None}
    if include_boxes:
        contents = row.package_contents or {}
        boxes = []
        for number in range(1, (row.pieces or 1) + 1):
            package = package_at(contents, number)
            box_weight = package.get('weight')
            if box_weight is None:
                box_weight = row.weight
            boxes.append({'number': number, 'pieces': package['pieces'],
                          'weight': str(box_weight) if box_weight is not None else None,
                          'weight_exception': package.get('weight') is not None,
                          'note': package.get('note', '')})
        shipment['boxes'] = boxes
    return shipment


def _selected_rows(values):
    if not isinstance(values, list) or not values or len(values) > 500:
        raise ValueError('Select between 1 and 500 saved shipments.')
    selected = list(dict.fromkeys(_label_record_id(value) for value in values))
    found = {row.id: row for row in models.shipment_query().filter(models.Consignment.id.in_(selected)).all()}
    if len(found) != len(selected):
        raise ValueError('A selected shipment no longer exists. Refresh the list and select again.')
    rows = [found[row_id] for row_id in selected]
    if sum(row.pieces or 1 for row in rows) > 500:
        raise ValueError('Selected shipments exceed 500 box/pallet labels. Select fewer shipments.')
    return rows


def _same_origin():
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise ValueError('Label requests must come from this admin app.')


def _review_details(values, rows):
    if not isinstance(values, list) or len(values) != len(rows):
        raise ValueError('Enter label details for every selected shipment.')
    details = {}
    rows_by_id = {str(row.id): row for row in rows}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Invalid shipment label details.')
        identifier = str(_label_record_id(item.get('id'), details=True))
        if identifier in details:
            raise ValueError('Label details include a duplicate shipment.')
        row = rows_by_id.get(identifier)
        if row is None:
            raise ValueError('Label details must match exactly the selected shipments.')
        def invoice_amount(raw, *, required):
            if raw in (None, '') and not required:
                return amount
            if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)) or len(str(raw)) > 20 or not str(raw).strip():
                raise ValueError('Enter a valid invoice amount for every box.')
            try:
                parsed = Decimal(str(raw).strip())
                if not parsed.is_finite() or parsed < 0 or parsed > Decimal('999999999.99') or parsed != parsed.quantize(Decimal('.01')):
                    raise ValueError('Invoice amounts must be between Rs 0 and Rs 999,999,999.99, with at most two decimal places.')
            except InvalidOperation:
                raise ValueError('Enter a valid invoice amount.') from None
            return parsed

        amount = invoice_amount(item.get('invoice_amount'), required=True)
        box_amounts = item.get('box_invoice_amounts')
        if box_amounts is None:
            box_amounts = [amount] * (row.pieces or 1)
        if not isinstance(box_amounts, list) or len(box_amounts) != (row.pieces or 1):
            raise ValueError('Enter an invoice value for every box/pallet.')
        box_amounts = [f'{invoice_amount(value, required=False):.2f}' for value in box_amounts]
        payment = item.get('payment_mode')
        if payment not in PAYMENT_MODES:
            raise ValueError('Choose Pre-paid, To pay or Cash on delivery for every shipment.')
        description = item.get('goods_description')
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 160 or any(ord(char) < 32 and char not in '\n\t' for char in description):
            raise ValueError('Enter a goods description of 1–160 characters for every shipment.')
        names = {}
        for key, title in (('sender_name', 'Sender name'), ('receiver_name', 'Receiver name')):
            name = item.get(key, '')
            if not isinstance(name, str) or len(name.strip()) > 120 or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise ValueError(f'{title} must be text of up to 120 characters.')
            # Omit blank names to keep previously saved label runs compatible.
            if name.strip():
                names[key] = name.strip()
        exists = item.get('eway_bill_exists', False)
        if not isinstance(exists, bool):
            raise ValueError('EWBN exists must be true or false.')
        eway = item.get('eway_bill_number', '') if exists else ''
        if exists and (not isinstance(eway, str) or not re.fullmatch(r'[0-9]{12}', eway.strip())):
            raise ValueError('Enter the 12-digit EWBN, or uncheck EWBN exists.')
        details[identifier] = {'invoice_amount': f'{amount:.2f}', 'box_invoice_amounts': box_amounts, 'payment_mode': payment,
                               'goods_description': description.strip(), 'eway_bill_exists': exists,
                               'eway_bill_number': eway.strip(), **names}
    if set(details) != {str(row.id) for row in rows}:
        raise ValueError('Label details must match exactly the selected shipments.')
    return [{**label_shipment(row, include_boxes=True), **details[str(row.id)]} for row in rows]


# Bundled fonts keep label printing independent of host fonts.
_font_dir = Path(__file__).resolve().parents[1] / 'static' / 'fonts' / 'labels'
for _name, _filename in (('LabelSans', 'LiberationSans-Regular.ttf'), ('LabelSansBold', 'LiberationSans-Bold.ttf'),
                         ('LabelMono', 'LiberationMono-Regular.ttf'), ('LabelMonoBold', 'LiberationMono-Bold.ttf')):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, str(_font_dir / _filename)))
pdfmetrics.registerFontFamily('LabelSans', normal='LabelSans', bold='LabelSansBold', italic='LabelSans', boldItalic='LabelSansBold')
pdfmetrics.registerFontFamily('LabelMono', normal='LabelMono', bold='LabelMonoBold', italic='LabelMono', boldItalic='LabelMonoBold')


def _label_rows():
    page = max(1, request.args.get('page', 1, type=int))
    search = request.args.get('search', '').strip()
    query = models.shipment_query()
    if search:
        query = query.filter(or_(models.Consignment.consignment_number.ilike(f'%{search}%'), models.Consignment.company.has(models.Company.name.ilike(f'%{search}%')),
                                models.Consignment.pickup_tag.ilike(f'%{search}%'), models.Consignment.drop_tag.ilike(f'%{search}%')))
    total = query.count()
    rows = query.order_by(models.Consignment.id.desc()).offset((page - 1) * 25).limit(25).all()
    return dict(rows=rows, label_page_rows=[label_shipment(row) for row in rows],
                label_id_format='uuid' if models.using_er() else 'integer', page=page, total=total, search=search)


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
        return jsonify(success=True, shipments=[label_shipment(row, include_boxes=True) for row in rows])
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400


def _fit_text(pdf, text, x, y, width, height, font='LabelSans', size=10, leading=None, alignment=0, text_color=colors.black):
    paragraph = Paragraph(text, ParagraphStyle('label', fontName=font, fontSize=size, leading=leading or size*1.2, textColor=text_color, alignment=alignment))
    box = KeepInFrame(width, height, [paragraph], mode='shrink')
    _, used_height = box.wrapOn(pdf, width, height)
    box.drawOn(pdf, x, y-used_height)
    return used_height


def _address_text(name, address, pin):
    # Preserve meaningful address lines, but discard blank lines and indenting.
    address_lines = [' '.join(line.split()) for line in (address or '').splitlines() if line.strip()]
    lines = ['<b>' + escape(' '.join(name.split())) + '</b>'] if name else []
    lines += [escape(line) for line in address_lines] or ['Address not provided']
    if pin and not re.search(r'(?<!\d)' + re.escape(pin) + r'(?!\d)', ' '.join(address_lines)):
        lines.append(escape(pin))
    return '<br/>'.join(lines)


def _address(pdf, title, name, address, pin, top, height, recipient=False):
    left, width = 18, 252
    pdf.setFont('LabelSansBold', 6.5)
    pdf.drawString(left, top, title)
    used_height = _fit_text(pdf, _address_text(name, address, pin), left, top-3, width, height,
                            font='LabelMono', size=9 if recipient else 8, leading=10.5 if recipient else 9.5)
    return top-3-used_height


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

            # Content determines address height, leaving only a small inset.
            # The shared budget reserves room for the barcode and footer even
            # with long names/addresses; short senders give the receiver room.
            sender_top = 365
            sender_bottom = _address(pdf, 'SHIP FROM', shipment.get('sender_name', ''), shipment['pickup_address'], shipment['pickup_pincode'], sender_top, 36)
            sender_height = sender_top-3-sender_bottom
            sender_rule = sender_bottom-5
            pdf.line(18, sender_rule, 270, sender_rule)
            recipient_bottom = _address(pdf, 'DELIVER TO', shipment.get('receiver_name', ''), shipment['drop_address'], shipment['drop_pincode'], sender_rule-10, 94-sender_height, recipient=True)
            address_rule = recipient_bottom-7
            pdf.line(18, address_rule, 270, address_rule)

            # Measurements, boxes, pickup date and piece count share one row.
            pdf.setFont('LabelSans', 6)
            volume_mode = shipment.get('measurement_mode') == 'volume'
            metric_heading, metric_value = address_rule-10, address_rule-17
            pdf.drawString(18, metric_heading, 'VOLUME' if volume_mode else 'WEIGHT')
            pdf.drawString(105, metric_heading, 'PIECES')
            pdf.drawString(145, metric_heading, 'PICKUP DATE')
            contents = shipment.get('package_contents') or {}
            package = package_at(contents, piece)
            pdf.drawString(237, metric_heading, 'PALLET' if contents.get('kind') == 'pallet' else 'BOX')
            box = shipment['boxes'][piece-1]
            value = shipment['volume'] if volume_mode else box['weight']
            unit = ('ft³' if shipment.get('volume_unit') == 'ft3' else 'm³') if volume_mode else 'kg'
            measurement = (value if value is not None else '—') + ' ' + unit
            if volume_mode and box['weight_exception']:
                measurement += ' / ' + (box['weight'] if box['weight'] is not None else '—') + ' kg'
            _fit_text(pdf, measurement, 18, metric_value, 79, 12, font='LabelMonoBold', size=9)
            _fit_text(pdf, str(package['pieces']) if package['pieces'] is not None else '—', 105, metric_value, 32, 12, font='LabelMonoBold', size=9)
            _fit_text(pdf, escape(shipment['pickup_date']), 145, metric_value, 84, 12, font='LabelMonoBold', size=8)
            _fit_text(pdf, f'{piece} of {pieces}', 237, metric_value, 33, 12, font='LabelMonoBold', size=7)
            tracking_top = address_rule-37
            pdf.setLineWidth(1)
            pdf.line(18, tracking_top, 270, tracking_top)
            pdf.setLineWidth(.5)

            # Human-readable tracking remains when the optional barcode is off.
            pdf.setFont('LabelSans', 6)
            pdf.drawCentredString(144, tracking_top-12, 'TRACKING NUMBER')
            if include_barcode:
                barcode = code128.Code128(shipment['consignment_number'], barHeight=tracking_top-174, barWidth=.8, humanReadable=False)
                pdf.saveState()
                scale = min(1.8, 252 / barcode.width)
                pdf.translate(18 + (252 - barcode.width * scale) / 2, 151)
                pdf.scale(scale, 1)
                barcode.drawOn(pdf, 0, 0)
                pdf.restoreState()
            _fit_text(pdf, escape(shipment['consignment_number']), 18, 151 if include_barcode else tracking_top-28,
                      252, 16 if include_barcode else tracking_top-158, font='LabelMonoBold',
                      size=14 if include_barcode else 18, leading=16 if include_barcode else 20, alignment=1)
            pdf.line(18, 128, 270, 128)

            # Compact two-column reference grid, followed by full-width goods.
            pdf.setFont('LabelSansBold', 6)
            for caption, baseline in [('INVOICE AMOUNT', 116), ('ORDER ID', 82), ('GOODS', 49)]:
                pdf.drawString(18, baseline, caption)
            pdf.drawString(152, 82, 'INVOICE NO.')
            _fit_text(pdf, 'Rs ' + f"{Decimal(shipment['box_invoice_amounts'][piece-1]):,.2f}", 18, 109, 118 if shipment['eway_bill_exists'] else 252, 13, font='LabelMonoBold', size=10)
            _fit_text(pdf, assignment['order_id'], 18, 75, 118, 12, font='LabelMono', size=7.5)
            _fit_text(pdf, assignment['invoice_number'], 152, 75, 118, 12, font='LabelMono', size=7.5)
            note = package.get('note', '')
            _fit_text(pdf, escape(shipment['goods_description']).replace('\n', '<br/>'), 18, 42, 252, 12 if note else 26, font='LabelMono', size=7.5, leading=8.5)
            if note:
                caption = 'PALLET NOTE:' if contents.get('kind') == 'pallet' else 'BOX NOTE:'
                _fit_text(pdf, '<b>' + caption + '</b> ' + escape(note), 18, 28, 252, 13, font='LabelMono', size=7, leading=8)
            pdf.setLineWidth(.4)
            pdf.line(18, 92, 270, 92)
            pdf.line(18, 59, 270, 59)
            pdf.line(144, 92, 144, 59)
            if shipment['eway_bill_exists']:
                pdf.setFont('LabelSansBold', 6)
                pdf.drawString(152, 116, 'EWBN:')
                _fit_text(pdf, shipment['eway_bill_number'], 152, 109, 118, 13, font='LabelMonoBold', size=9)
                pdf.line(144, 128, 144, 92)
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
