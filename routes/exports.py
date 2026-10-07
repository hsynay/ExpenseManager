"""Excel downloads for the four list pages.

Each export reads the same query string as its page (filters, search, sort)
and builds its rows with the same helpers the page uses, but it ignores the
paging parameters, so every matching record is in the file. All of them are
plain GET routes that only read.
"""
from flask import request, redirect, url_for, flash
from db import get_connection
from core import app
from helpers import login_required
from excel_export import ExcelBook, TEXT, MONEY, DATE
from routes.income import _payments_query, _load_debt_status
from routes.checks import _incoming_checks_query, _outgoing_checks_query
from routes.expenses import (
    _expense_children, _large_expenses, _petty_items, _build_expense_cards,
)

CHECK_STATUS_LABELS = {
    'portfoyde': 'Portföyde',
    'tahsil_edildi': 'Tahsil Edildi',
    'verildi': 'Verildi',
    'odendi': 'Ödendi',
    'karsiliksiz': 'Karşılıksız',
}
PROJECT_TYPE_LABELS = {'normal': 'Normal', 'cooperative': 'Kooperatif'}


def _method_label(method):
    return 'Çek' if method == 'çek' else 'Nakit'


def _check_status_label(method, status):
    """The check status of a payment; empty for cash."""
    if method != 'çek' or not status:
        return None
    return CHECK_STATUS_LABELS.get(status, status)


def _flat_label(block_name, floor, flat_no):
    return 'Blok: %s, Kat: %s, No: %s' % (block_name or 'N/A', floor, flat_no)


@app.route('/payments/export')
@login_required
def export_payments():
    sql, params = _payments_query(request.args)

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        payments = cur.fetchall()
    except Exception:
        app.logger.exception('Failed to export payments')
        flash('Excel dosyası hazırlanırken bir hata oluştu.', 'danger')
        return redirect(url_for('list_payments'))
    finally:
        cur.close()
        conn.close()

    columns = [('Proje', TEXT), ('Müşteri', TEXT), ('Daire', TEXT),
               ('Ödeme Yöntemi', TEXT), ('Açıklama', TEXT),
               ('Ücret (₺)', MONEY), ('Tarih', DATE)]
    rows = []
    for (_id, project, first_name, last_name, flat_no, floor, _installment,
         amount, paid_on, block_name, method, description) in payments:
        rows.append([project, '%s %s' % (first_name, last_name),
                     _flat_label(block_name, floor, flat_no),
                     _method_label(method), description, amount, paid_on])

    book = ExcelBook()
    book.add_sheet('Gelirler', columns, rows)
    return book.response('payments')


@app.route('/checks/export')
@login_required
def export_checks():
    conn = get_connection()
    cur = conn.cursor()
    try:
        in_sql, in_params = _incoming_checks_query(request.args)
        cur.execute(in_sql, tuple(in_params))
        incoming = cur.fetchall()

        out_sql, out_params = _outgoing_checks_query(request.args)
        cur.execute(out_sql, tuple(out_params))
        outgoing = cur.fetchall()
    except ValueError:
        # Only a hand edited date in the address can cause this.
        flash('Geçersiz tarih. Excel dosyası hazırlanamadı.', 'danger')
        return redirect(url_for('list_checks'))
    except Exception:
        app.logger.exception('Failed to export checks')
        flash('Excel dosyası hazırlanırken bir hata oluştu.', 'danger')
        return redirect(url_for('list_checks'))
    finally:
        cur.close()
        conn.close()

    def check_rows(checks):
        return [[due_date, party, bank, number, amount,
                 CHECK_STATUS_LABELS.get(status, status)]
                for _id, due_date, amount, party, bank, number, status in checks]

    def check_columns(party_header):
        return [('Vade Tarihi', DATE), (party_header, TEXT), ('Banka', TEXT),
                ('Çek No', TEXT), ('Tutar', MONEY), ('Durum', TEXT)]

    book = ExcelBook()
    book.add_sheet('Alınan Çekler', check_columns('Veren Müşteri'),
                   check_rows(incoming))
    book.add_sheet('Verilen Çekler', check_columns('Verilen Kişi / Kurum'),
                   check_rows(outgoing))
    return book.response('checks')


@app.route('/expenses/export')
@login_required
def export_expenses():
    # Like the page, the export is for one project at a time.
    try:
        project_id = int(request.args.get('project_id'))
    except (TypeError, ValueError):
        flash('Excel için önce bir proje seçin.', 'danger')
        return redirect(url_for('list_expenses'))

    expense_type = request.args.get('expense_type', 'all')
    title_filter = (request.args.get('title') or '').strip()
    supplier_id_str = request.args.get('supplier_id')
    pc_sort = request.args.get('pc_sort', 'date')
    pc_order = request.args.get('pc_order', 'desc')
    if pc_order not in ['asc', 'desc']:
        pc_order = 'desc'

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        if not cur.fetchone():
            flash('Proje bulunamadı.', 'danger')
            return redirect(url_for('list_expenses'))

        cards, petty_items = [], []
        if expense_type in ('all', 'large'):
            payments_by_expense, schedules_by_expense = _expense_children(
                cur, project_id)
            cards = _build_expense_cards(
                _large_expenses(cur, project_id, supplier_id_str, title_filter),
                schedules_by_expense, payments_by_expense)
        if expense_type in ('all', 'petty'):
            petty_items = _petty_items(cur, project_id, title_filter,
                                       pc_sort, pc_order)
    except Exception:
        app.logger.exception('Failed to export expenses (project_id=%s)',
                             project_id)
        flash('Excel dosyası hazırlanırken bir hata oluştu.', 'danger')
        return redirect(url_for('list_expenses', project_id=project_id))
    finally:
        cur.close()
        conn.close()

    book = ExcelBook()
    if expense_type in ('all', 'large'):
        expense_rows, installment_rows, payment_rows = [], [], []
        for card in cards:
            who = [card['title'], card['supplier_name']]
            expense_rows.append(who + [card['total_amount'], card['total_paid'],
                                       card['remaining_due']])
            for inst in card['installments']:
                installment_rows.append(who + [
                    inst['due_date'], inst['total_amount'],
                    inst['remaining_installment_due'], inst['status']])
            for (_expense_id, _id, paid_on, description, amount, method,
                 _check_id, status, check_number, check_due,
                 bank_name) in card['payments']:
                payment_rows.append(who + [
                    paid_on, description, _method_label(method),
                    _check_status_label(method, status), bank_name,
                    check_number, check_due, amount])

        book.add_sheet('Büyük Giderler',
                       [('Gider', TEXT), ('Tedarikçi', TEXT),
                        ('Toplam Borç', MONEY), ('Toplam Ödenen', MONEY),
                        ('Kalan Borç', MONEY)], expense_rows)
        book.add_sheet('Gider Taksitleri',
                       [('Gider', TEXT), ('Tedarikçi', TEXT),
                        ('Vade Tarihi', DATE), ('Taksit Tutarı', MONEY),
                        ('Kalan', MONEY), ('Durum', TEXT)], installment_rows)
        book.add_sheet('Gider Ödemeleri',
                       [('Gider', TEXT), ('Tedarikçi', TEXT),
                        ('Ödeme Tarihi', DATE), ('Açıklama', TEXT),
                        ('Yöntem', TEXT), ('Çek Durumu', TEXT),
                        ('Banka', TEXT), ('Çek No', TEXT),
                        ('Çek Vadesi', DATE), ('Tutar', MONEY)], payment_rows)
    if expense_type in ('all', 'petty'):
        book.add_sheet('Küçük Giderler',
                       [('Tarih', DATE), ('Açıklama', TEXT), ('Tutar', MONEY)],
                       [[spent_on, title, amount]
                        for _id, title, amount, spent_on, _description
                        in petty_items])
    return book.response('expenses')


@app.route('/debts/export')
@login_required
def export_debts():
    project_filter = request.args.get('project_id', type=int)
    flat_filter = request.args.get('flat_id', type=int)
    search_query = (request.args.get('search') or '').strip()

    conn = get_connection()
    cur = conn.cursor()
    try:
        data = _load_debt_status(cur, project_filter, flat_filter, search_query)
    except Exception:
        app.logger.exception('Failed to export the debt status')
        flash('Excel dosyası hazırlanırken bir hata oluştu.', 'danger')
        return redirect(url_for('debt_status'))
    finally:
        cur.close()
        conn.close()

    project_rows, flat_rows, installment_rows, payment_rows = [], [], [], []
    for project in data['projects_data']:
        is_normal = project['project_type'] == 'normal'
        project_name = project['project_name']
        # The page only shows project totals for normal projects.
        project_rows.append([
            project_name,
            PROJECT_TYPE_LABELS.get(project['project_type'],
                                    project['project_type']),
            project['total_project_income'] if is_normal else None,
            project['total_project_paid'] if is_normal else None,
            project['total_project_remaining'] if is_normal else None])

        for flat in project['flats']:
            who = [project_name, flat['customer_name'], flat['flat_details']]
            # A cooperative flat only shows what the member has paid so far.
            flat_rows.append(who + [
                flat['flat_total_price'] if is_normal else None,
                flat['total_paid'],
                flat['remaining_debt'] if is_normal else None])
            for inst in flat['installments']:
                installment_rows.append(who + [
                    inst['due_date'], inst['total_amount'],
                    inst['remaining_installment_due'], inst['status']])
            for (_id, _flat_id, paid_on, description, amount, method, status,
                 bank_name, check_number, check_due,
                 _check_id) in flat['payments']:
                payment_rows.append(who + [
                    paid_on, description, _method_label(method),
                    _check_status_label(method, status), bank_name,
                    check_number, check_due, amount])

    who_columns = [('Proje', TEXT), ('Müşteri', TEXT), ('Daire', TEXT)]
    book = ExcelBook()
    book.add_sheet('Projeler',
                   [('Proje', TEXT), ('Proje Türü', TEXT),
                    ('Proje Toplam Geliri', MONEY),
                    ('Tahsil Edilen Gelir', MONEY),
                    ('Kalan Alacak', MONEY)], project_rows)
    book.add_sheet('Daireler',
                   who_columns + [('Toplam Borç', MONEY),
                                  ('Toplam Ödenen', MONEY),
                                  ('Kalan Borç', MONEY)], flat_rows)
    book.add_sheet('Taksitler',
                   who_columns + [('Vade Tarihi', DATE),
                                  ('Taksit Tutarı', MONEY),
                                  ('Kalan', MONEY), ('Durum', TEXT)],
                   installment_rows)
    book.add_sheet('Ödemeler',
                   who_columns + [('Ödeme Tarihi', DATE),
                                  ('Açıklama', TEXT), ('Yöntem', TEXT),
                                  ('Çek Durumu', TEXT), ('Banka', TEXT),
                                  ('Çek No', TEXT), ('Çek Vadesi', DATE),
                                  ('Tutar', MONEY)], payment_rows)
    return book.response('debts')
