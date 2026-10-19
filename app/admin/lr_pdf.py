"""Fill the supplied two-page LR artwork without rebuilding its fixed sections."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepInFrame, Paragraph

from app.admin.lr_fields import FIELDS

ASSETS = Path(__file__).resolve().parents[1]
pdfmetrics.registerFont(TTFont('LRSans', str(ASSETS / 'static/fonts/labels/LiberationSans-Regular.ttf')))


def _text(pdf, value, box, size=8):
    if not value:
        return
    left, top, right, bottom = box
    paragraph = Paragraph(escape(str(value)).replace('\n', '<br/>'),
                          ParagraphStyle('LR', fontName='LRSans', fontSize=size, leading=size * 1.15))
    fitted = KeepInFrame(right - left, bottom - top, [paragraph], mode='shrink')
    _, height = fitted.wrapOn(pdf, right - left, bottom - top)
    fitted.drawOn(pdf, left, 841.89 - top - height)


def _display(field, value):
    if not value:
        return ''
    if field['kind'] == 'date':
        return datetime.strptime(value, '%Y-%m-%d').strftime('%d-%b-%Y')
    if field['kind'] == 'money':
        # Indian digit grouping, as in the supplied document.
        whole, fraction = value.split('.')
        tail, whole = whole[-3:], whole[:-3]
        groups = []
        while whole:
            groups.insert(0, whole[-2:])
            whole = whole[:-2]
        return ','.join([*groups, tail]) + '.' + fraction
    return value


def generate_lr_pdf(receipts, generated_at=None):
    """One original and its unchanged terms page per shipment, entirely in memory."""
    template = PdfReader(ASSETS / 'assets/lr-template.pdf')
    writer = PdfWriter()
    timestamp = (generated_at or datetime.now(ZoneInfo('Asia/Kolkata'))).astimezone(ZoneInfo('Asia/Kolkata'))
    for receipt in receipts:
        # Each LR needs its own page/content objects; otherwise repeated source
        # pages can share an indirect stream and overwrite an earlier shipment.
        writer.reset_translation(template)
        for page_index in range(2):
            overlay = BytesIO()
            pdf = canvas.Canvas(overlay, pagesize=(595.276, 841.89))
            if page_index == 0:
                for item in FIELDS:
                    if item['box'] is None:
                        continue
                    value = _display(item, receipt[item['name']])
                    if item['name'] == 'packages':
                        value += ' ' + receipt['package_type']
                    _text(pdf, value, item['box'], 10 if item['name'] == 'lr_number' else 8)
                _text(pdf, _display({'kind': 'money'}, receipt['invoice_value']), (205, 302, 293, 314))
                barcode = Code128(receipt['lr_number'], barHeight=25, barWidth=.8, humanReadable=False)
                pdf.saveState()
                pdf.translate(400, 785)
                pdf.scale(min(1, 176 / barcode.width), 1)
                barcode.drawOn(pdf, 0, 0)
                pdf.restoreState()
            _text(pdf, timestamp.strftime('%d-%m-%Y %H:%M:%S IST'), (16.4, 825, 260, 838))
            _text(pdf, f'{page_index + 1} Of 2', (546, 825, 580, 838))
            pdf.save()
            overlay.seek(0)
            page = writer.add_page(template.pages[page_index])
            page.merge_page(PdfReader(overlay).pages[0])
    writer.add_metadata({'/Title': 'GRAM SCS Lorry Receipts'})
    result = BytesIO()
    writer.write(result)
    result.seek(0)
    return result
