"""Write spreadsheet downloads without openpyxl's temporary worksheet files."""
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl.drawing.spreadsheet_drawing import SpreadsheetDrawing
from openpyxl.worksheet._writer import WorksheetWriter
from openpyxl.writer.excel import ExcelWriter


class _MemoryExcelWriter(ExcelWriter):
    def write_worksheet(self, worksheet):
        # openpyxl 3.1.5 normally writes worksheet XML to a NamedTemporaryFile,
        # even when Workbook.save() receives BytesIO. Use its stream parameter.
        worksheet._drawing = SpreadsheetDrawing()
        worksheet._drawing.charts = worksheet._charts
        worksheet._drawing.images = worksheet._images
        with BytesIO() as stream:
            writer = WorksheetWriter(worksheet, out=stream)
            writer.write()
            worksheet._rels = writer._rels
            self._archive.writestr(worksheet.path[1:], stream.getvalue())
        self.manifest.append(worksheet)


def save_workbook_in_memory(workbook, output):
    if workbook.write_only:
        raise ValueError('Use a normal workbook for memory-only spreadsheet output.')
    with ZipFile(output, 'w', ZIP_DEFLATED, allowZip64=True) as archive:
        _MemoryExcelWriter(workbook, archive).save()
