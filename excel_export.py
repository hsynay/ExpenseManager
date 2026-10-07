"""Excel downloads for the list pages.

Every export looks the same: a bold header row, money columns stored as real
numbers with a thousands format (so Excel can still add them up) and dates
shown as DD.MM.YYYY.

Usage:
    book = ExcelBook()
    book.add_sheet('Odemeler', [('Proje', TEXT), ('Tutar', MONEY)], rows)
    return book.response('payments')
"""
from datetime import date
from io import BytesIO

from flask import send_file
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MONEY_FORMAT = '#,##0.00'
DATE_FORMAT = 'DD.MM.YYYY'

# Column kinds
TEXT, MONEY, DATE = 'text', 'money', 'date'

MAX_COLUMN_WIDTH = 60
HEADER_FONT = Font(bold=True)


class ExcelBook:
    """One .xlsx file made of one or more sheets."""

    def __init__(self):
        self._workbook = Workbook()
        # A new workbook starts with one empty sheet. The first add_sheet
        # call renames it instead of leaving it behind.
        self._unused_first_sheet = True

    def add_sheet(self, title, columns, rows):
        """Write one sheet.

        columns is a list of (header, kind) pairs, rows is a list of
        sequences in the same order. A row with the wrong length raises an
        error, so a column that drifts out of step cannot go unnoticed.
        """
        if self._unused_first_sheet:
            sheet = self._workbook.active
            sheet.title = title
            self._unused_first_sheet = False
        else:
            sheet = self._workbook.create_sheet(title)

        widths = [len(header) for header, _ in columns]
        for col, (header, _) in enumerate(columns, start=1):
            sheet.cell(row=1, column=col, value=header).font = HEADER_FONT

        for row_number, row in enumerate(rows, start=2):
            if len(row) != len(columns):
                raise ValueError('Excel row has %d values for %d columns'
                                 % (len(row), len(columns)))
            for col, ((_, kind), value) in enumerate(zip(columns, row), start=1):
                cell = sheet.cell(row=row_number, column=col)
                shown = self._fill(cell, kind, value)
                widths[col - 1] = max(widths[col - 1], len(shown))

        for col, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(col)].width = \
                min(width + 2, MAX_COLUMN_WIDTH)

    @staticmethod
    def _fill(cell, kind, value):
        """Put one value in a cell. Returns the text used to size the column."""
        if value is None:
            return ''
        if kind == MONEY:
            cell.value = value
            cell.number_format = MONEY_FORMAT
            return '{:,.2f}'.format(value)
        if kind == DATE:
            cell.value = value
            cell.number_format = DATE_FORMAT
            return 'DD.MM.YYYY'
        text = str(value)
        cell.value = text
        # openpyxl turns any text that starts with "=" into a formula. Names
        # and descriptions are typed by users, so force plain text.
        cell.data_type = 's'
        return text

    def response(self, page_name):
        """The finished file as a download named page_name_YYYY-MM-DD.xlsx."""
        buffer = BytesIO()
        self._workbook.save(buffer)
        buffer.seek(0)
        filename = '%s_%s.xlsx' % (page_name, date.today().isoformat())
        response = send_file(buffer, mimetype=XLSX_MIME, as_attachment=True,
                             download_name=filename)
        # Financial data: no browser or proxy should keep a copy.
        response.headers['Cache-Control'] = 'no-store'
        return response
