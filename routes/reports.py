"""Reports, the cooperative report, the dashboard and the audit log."""
from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from db import get_connection
from datetime import date
import json
from decimal import Decimal
from core import app
from helpers import login_required, json_login_required


@app.route('/reports/cooperative/select', methods=['GET', 'POST'])
@login_required
def select_project_for_coop_report():
    """Kooperatif raporu için proje seçim sayfası."""
    if request.method == 'POST':
        project_id = request.form.get('project_id')
        if project_id:
            report_month = request.form.get('report_month') 
            year, month = map(int, report_month.split('-'))
            return redirect(url_for('cooperative_report', project_id=project_id, year=year, month=month))
        else:
            flash("Lütfen bir proje seçin.", "warning")
    
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Sadece kooperatif projelerini listele
        cur.execute("SELECT id, name FROM projects WHERE project_type = 'cooperative' ORDER BY name")
        projects = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    
    # Varsayılan olarak bir önceki ayı seçili getir
    last_month = date.today().replace(day=1) - relativedelta(days=1)
    default_month = last_month.strftime('%Y-%m')

    return render_template('select_project_coop.html', 
                           projects=projects,
                           default_month=default_month,
                           user_name=session.get('user_name'))

# cooperative_report fonksiyonu

@app.route('/reports/cooperative/<int:project_id>/<int:year>/<int:month>')
@login_required
def cooperative_report(project_id, year, month):
    """Belirli bir kooperatif projesinin aylık finansal raporunu gösterir."""
    conn = get_connection()
    cur = conn.cursor()
    report_data = {}

    # Türkçe ay isimleri için sözlük ---
    turkish_months = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
        7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
    }

    try:
        start_date = date(year, month, 1)
        end_date = (start_date + relativedelta(months=1)) - relativedelta(days=1)

        # Proje bilgilerini al
        cur.execute("SELECT name, total_flats, project_type FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()

        if not project_info:
            flash("Rapor istenen proje bulunamadı.", "warning")
            return redirect(url_for('select_project_for_coop_report'))

        # This report reads every payment as a monthly due (aidat) and carries a
        # balance from month to month. That only makes sense for a cooperative.
        # For a normal project it would still produce numbers, and they would
        # look right while meaning nothing, so we stop here instead. The select
        # page lists cooperative projects only, but a bookmarked or hand typed
        # URL can still reach this route.
        if project_info[2] != 'cooperative':
            flash("Bu rapor sadece kooperatif projeler içindir.", "warning")
            return redirect(url_for('select_project_for_coop_report'))

        report_data['project_name'] = project_info[0]
        
        # Üye sayısını (sahibi olan daire sayısı) al
        cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s AND owner_id IS NOT NULL", (project_id,))
        member_count = cur.fetchone()[0]
        report_data['member_count'] = member_count

        # Business rule for every total on this page:
        # money counts only when it really moved. An incoming check counts
        # when it is marked 'tahsil_edildi', an outgoing check when it is
        # marked 'odendi'. A check that is still in the portfolio
        # ('portfoyde' / 'verildi') closes nothing, and a bounced check
        # ('karsiliksiz') never counts. The due date is information only:
        # nothing happens automatically when it passes.

        # 1. Önceki Aydan Devreden Bakiyeyi Hesapla
        cur.execute("""
            SELECT COALESCE(SUM(p.amount), 0)
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE f.project_id = %s AND p.payment_date < %s
              AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
        """, (project_id, start_date))
        total_income_before = cur.fetchone()[0]

        # Önceki aydan devreden giderler her iki tablodan toplanıyor
        cur.execute("""
            SELECT COALESCE(SUM(e.amount), 0)
            FROM expenses e
            LEFT JOIN outgoing_checks oc ON e.outgoing_check_id = oc.id
            WHERE e.project_id = %s AND e.expense_date < %s
              AND (e.payment_method = 'nakit' OR oc.status = 'odendi')
        """, (project_id, start_date))
        total_large_expense_before = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date < %s", (project_id, start_date))
        total_petty_cash_before = cur.fetchone()[0]
        total_expense_before = total_large_expense_before + total_petty_cash_before
        
        previous_balance = total_income_before - total_expense_before
        report_data['previous_balance'] = previous_balance

        # 2. Bu Ayın Gelir ve Giderlerini Hesapla (aynı kural: gerçekten hareket
        # etmiş para)
        cur.execute("""
            SELECT COALESCE(SUM(p.amount), 0)
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s
              AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
        """, (project_id, start_date, end_date))
        current_income = cur.fetchone()[0]
        report_data['current_income'] = current_income

        # Rapor ayına ait giderler her iki tablodan toplanıyor
        cur.execute("""
            SELECT COALESCE(SUM(e.amount), 0)
            FROM expenses e
            LEFT JOIN outgoing_checks oc ON e.outgoing_check_id = oc.id
            WHERE e.project_id = %s AND e.expense_date BETWEEN %s AND %s
              AND (e.payment_method = 'nakit' OR oc.status = 'odendi')
        """, (project_id, start_date, end_date))
        current_large_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        current_petty_cash_expense = cur.fetchone()[0]
        current_expense = current_large_expense + current_petty_cash_expense
        report_data['current_expense'] = current_expense
        
        # 3. Ay Sonu Bakiyesini Hesapla
        end_of_month_balance = previous_balance + current_income - current_expense
        report_data['end_of_month_balance'] = end_of_month_balance

        # 4. Detaylı listeler için verileri çek
        #
        # Every row is listed, but only money that really moved is added to the
        # totals. Each row carries a "counts" flag:
        #   counts = True   -> cash, or a check marked tahsil_edildi / odendi
        #   counts = False  -> a check still in the portfolio, or a bounced one
        # A bounced check is shown as well, but it is in no total at all,
        # because that money will never arrive.
        cur.execute("""
            SELECT p.payment_date, c.first_name, c.last_name,
                   f.block_name, f.floor, f.flat_no,
                   p.description, p.amount, p.payment_method, ch.status
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN customers c ON f.owner_id = c.id
            LEFT JOIN checks ch ON p.check_id = ch.id
            WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s
            ORDER BY p.payment_date
        """, (project_id, start_date, end_date))

        income_details = []
        income_collected = Decimal(0)
        income_pending = Decimal(0)
        for (pay_date, first_name, last_name, block, floor, flat_no,
             description, amount, method, check_status) in cur.fetchall():
            amount = amount or Decimal(0)

            if method == 'çek' and check_status == 'karsiliksiz':
                status_text, status_class, counts = 'Karşılıksız', 'danger', False
            elif method == 'çek' and check_status != 'tahsil_edildi':
                status_text, status_class, counts = 'Çek Portföyde', 'warning text-dark', False
                income_pending += amount
            else:
                status_text, status_class, counts = 'Tahsil Edildi', 'success', True
                income_collected += amount

            income_details.append({
                'date': pay_date,
                # The flat may have no owner yet. The payment still belongs to
                # the project total, so we list it instead of hiding it.
                'customer': f"{first_name} {last_name}" if first_name else "Sahibi atanmamış",
                'flat': f"Blok: {block or 'N/A'}, Kat: {floor}, No: {flat_no}",
                'description': description,
                'method': method,
                'status_text': status_text,
                'status_class': status_class,
                'amount': amount,
                'counts': counts,
            })

        report_data['income_details'] = income_details
        report_data['income_collected'] = income_collected
        report_data['income_pending'] = income_pending

        # Gider detayları listesi her iki tablodan birleştirilip tarihe göre sıralanıyor
        cur.execute("""
            SELECT e.expense_date, e.title, s.name, e.description, e.amount,
                   e.payment_method, oc.status
            FROM expenses e
            LEFT JOIN suppliers s ON e.supplier_id = s.id
            LEFT JOIN outgoing_checks oc ON e.outgoing_check_id = oc.id
            WHERE e.project_id = %s AND e.expense_date BETWEEN %s AND %s
        """, (project_id, start_date, end_date))

        expense_details = []
        expense_paid = Decimal(0)
        expense_pending = Decimal(0)
        for (exp_date, title, supplier_name, description, amount, method,
             check_status) in cur.fetchall():
            amount = amount or Decimal(0)

            if method == 'çek' and check_status == 'karsiliksiz':
                status_text, status_class, counts = 'Karşılıksız', 'danger', False
            elif method == 'çek' and check_status != 'odendi':
                status_text, status_class, counts = 'Çek Verildi', 'warning text-dark', False
                expense_pending += amount
            else:
                status_text, status_class, counts = 'Ödendi', 'success', True
                expense_paid += amount

            expense_details.append({
                'date': exp_date,
                'type': 'Büyük Gider',
                'title': title,
                'supplier': supplier_name or 'Belirtilmemiş',
                'description': description,
                'method': method,
                'status_text': status_text,
                'status_class': status_class,
                'amount': amount,
                'counts': counts,
            })

        # Petty cash is always money leaving the safe, so it always counts.
        cur.execute("""
            SELECT expense_date, title, description, amount
            FROM petty_cash_expenses
            WHERE project_id = %s AND expense_date BETWEEN %s AND %s
        """, (project_id, start_date, end_date))
        for exp_date, title, description, amount in cur.fetchall():
            amount = amount or Decimal(0)
            expense_paid += amount
            expense_details.append({
                'date': exp_date,
                'type': 'Küçük Gider',
                'title': title,
                'supplier': 'Kasa',
                'description': description,
                'method': 'nakit',
                'status_text': 'Ödendi',
                'status_class': 'success',
                'amount': amount,
                'counts': True,
            })

        expense_details.sort(key=lambda x: x['date'])
        report_data['expense_details'] = expense_details
        report_data['expense_paid'] = expense_paid
        report_data['expense_pending'] = expense_pending
        
        month_name = turkish_months.get(start_date.month, "")
        report_data['report_period'] = f"{month_name} {start_date.year}"

    except Exception:
        app.logger.exception('Failed to build cooperative report for project '
                             '%s (%s-%s)', project_id, year, month)
        flash("Rapor oluşturulurken bir hata oluştu. Rapor eksik olabilir.",
              "danger")
    finally:
        cur.close()
        conn.close()

    return render_template('coop_report.html', 
                           report_data=report_data,
                           user_name=session.get('user_name'))


@app.route('/audit-logs')
@login_required
def audit_logs():
    action_filter = request.args.get('action', '').strip()
    entity_filter = request.args.get('entity_type', '').strip()
    user_filter = request.args.get('user', '').strip()

    conn = get_connection()
    cur = conn.cursor()
    try:
        sql = """
            SELECT al.id, al.created_at, al.action, al.entity_type, al.entity_id,
                   al.user_id, COALESCE(u.full_name, u.email) as user_name,
                   al.details
            FROM audit_logs al
            LEFT JOIN users u ON u.id = al.user_id
            WHERE 1=1
        """
        params = []
        if action_filter:
            sql += " AND al.action = %s"
            params.append(action_filter)
        if entity_filter:
            sql += " AND al.entity_type = %s"
            params.append(entity_filter)
        if user_filter:
            sql += " AND (LOWER(u.full_name) LIKE LOWER(%s) OR LOWER(u.email) LIKE LOWER(%s))"
            like = f"%{user_filter}%"
            params.extend([like, like])
        sql += " ORDER BY al.created_at DESC LIMIT 500"
        cur.execute(sql, tuple(params))
        raw_rows = cur.fetchall()

        def summarize(log):
            act = log['action']
            et = log['entity_type']
            det = log['details'] or {}
            eid = log['entity_id']
            if act == 'project_create':
                return f"Yeni proje: {det.get('name','?')} (tip: {det.get('type','?')}, kat: {det.get('floors','?')}, daire: {det.get('flats','?')})"
            if act == 'project_delete':
                return f"Proje silindi: {det.get('name','?')} (gelir çekleri: {len(det.get('incoming_checks',[]) or [])}, gider çekleri: {len(det.get('outgoing_checks',[]) or [])})"
            if act == 'plan_update' and et == 'payment_plan':
                return f"Gelir ödeme planı güncellendi (daire #{eid}, taksit: {det.get('rows','?')}, toplam: {det.get('total_price','?')})"
            if act == 'plan_update' and et == 'expense_plan':
                return f"Gider ödeme planı güncellendi (gider #{eid}, taksit: {det.get('rows','?')}, toplam: {det.get('total_amount','?')})"
            if act == 'payment_create':
                return f"Ödeme eklendi (daire #{det.get('flat_id')}, {det.get('amount')} ₺, yöntem: {det.get('method')})"
            if act == 'payment_delete':
                return f"Ödeme silindi (payment #{eid}, daire #{det.get('flat_id')})"
            if act == 'supplier_payment_create':
                return f"Tedarikçi ödemesi eklendi (tedarikçi #{det.get('supplier_id')}, proje #{det.get('project_id')}, {det.get('amount')} ₺)"
            if act == 'supplier_payment_delete':
                return f"Tedarikçi ödemesi silindi (ödeme #{eid}, gider #{det.get('expense_id')})"
            if act == 'expense_delete':
                return f"Gider silindi (gider #{eid}, bağlı çek: {det.get('outgoing_check_id')})"
            if act == 'check_status_update':
                return f"Çek durumu değişti ({'Alınan' if et=='incoming_check' else 'Verilen'} çek #{eid}, yeni durum: {det.get('new_status')})"
            return f"{act} ({et} #{eid})"

        rows = []
        for r in raw_rows:
            details_obj = None
            if r[7] is not None:
                try:
                    details_obj = json.loads(r[7]) if isinstance(r[7], str) else r[7]
                except Exception:
                    details_obj = str(r[7])
            rows.append({
                'id': r[0],
                'created_at': r[1],
                'action': r[2],
                'entity_type': r[3],
                'entity_id': r[4],
                'user_id': r[5],
                'user_name': r[6],
                'details': details_obj
            })
            rows[-1]['summary'] = summarize(rows[-1])

        # benzersiz action ve entity listeleri filtre için
        cur.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action")
        actions = [a[0] for a in cur.fetchall()]
        cur.execute("SELECT DISTINCT entity_type FROM audit_logs ORDER BY entity_type")
        entities = [e[0] for e in cur.fetchall()]

    except Exception:
        app.logger.exception('Failed to load audit logs')
        flash("İşlem kayıtları yüklenirken bir hata oluştu. Liste eksik "
              "olabilir.", "danger")
        rows, actions, entities = [], [], []
    finally:
        cur.close()
        conn.close()

    return render_template(
        'audit_logs.html',
        logs=rows,
        actions=actions,
        entities=entities,
        sel_action=action_filter,
        sel_entity=entity_filter,
        sel_user=user_filter,
        user_name=session.get('user_name')
    )
@app.route('/reports')
@login_required
def reports():
    conn = get_connection()
    cur = conn.cursor()
    today = date.today()
    start_month = date(today.year, today.month, 1)

    def add_months(base_date, months):
        """Month-safe increment/decrement without external deps."""
        y = base_date.year + (base_date.month - 1 + months) // 12
        m = (base_date.month - 1 + months) % 12 + 1
        d = min(base_date.day, monthrange(y, m)[1])
        return date(y, m, d)

    # These go to the template, so they must exist even when a query fails.
    # Otherwise render_template below raises UnboundLocalError and the user
    # never sees the error message we prepared.
    project_summaries = []
    monthly_series = {}
    check_series = {}
    projection_series = {}
    overdue_items = {}
    month_boxes = {}

    try:
        cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
        projects = cur.fetchall()

        # --- 12 month charts, for every project at once ---------------------
        # Each chart used to run its own query per project and per month.
        # These five queries return the same sums grouped by project and
        # month; the project loop below only looks them up. Every query keeps
        # the joins and filters of the one it replaced, so no number changes.
        chart_months = [add_months(start_month, -offset)
                        for offset in range(11, -1, -1)]
        window_start = chart_months[0]
        window_end = add_months(start_month, 1)
        window = (window_start, window_end)

        # Real income: cash and cleared checks.
        cur.execute("""
            SELECT f.project_id,
                   date_trunc('month', p.payment_date)::date,
                   COALESCE(SUM(p.amount), 0)
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
              AND p.payment_date >= %s AND p.payment_date < %s
            GROUP BY 1, 2
        """, window)
        chart_income = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        # Real expense, large: cash and paid outgoing checks.
        cur.execute("""
            SELECT e.project_id,
                   date_trunc('month', sp.payment_date)::date,
                   COALESCE(SUM(sp.amount), 0)
            FROM supplier_payments sp
            JOIN expenses e ON sp.expense_id = e.id
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
            WHERE (sp.payment_method = 'nakit' OR oc.status = 'odendi')
              AND sp.payment_date >= %s AND sp.payment_date < %s
            GROUP BY 1, 2
        """, window)
        chart_large = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        # Real expense, petty cash.
        cur.execute("""
            SELECT project_id,
                   date_trunc('month', expense_date)::date,
                   COALESCE(SUM(amount), 0)
            FROM petty_cash_expenses
            WHERE expense_date >= %s AND expense_date < %s
            GROUP BY 1, 2
        """, window)
        chart_petty = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        # Incoming checks by due month, in the portfolio and cleared.
        cur.execute("""
            SELECT f.project_id, c.status,
                   date_trunc('month', c.due_date)::date,
                   COALESCE(SUM(c.amount), 0)
            FROM checks c
            JOIN payments p ON c.id = p.check_id
            JOIN flats f ON p.flat_id = f.id
            WHERE c.status IN ('portfoyde', 'tahsil_edildi')
              AND c.due_date >= %s AND c.due_date < %s
            GROUP BY 1, 2, 3
        """, window)
        chart_in_checks = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}

        # Outgoing checks by due month, handed over and paid.
        cur.execute("""
            SELECT e.project_id, oc.status,
                   date_trunc('month', oc.due_date)::date,
                   COALESCE(SUM(oc.amount), 0)
            FROM outgoing_checks oc
            JOIN supplier_payments sp ON oc.id = sp.check_id
            JOIN expenses e ON sp.expense_id = e.id
            WHERE oc.status IN ('verildi', 'odendi')
              AND oc.due_date >= %s AND oc.due_date < %s
            GROUP BY 1, 2, 3
        """, window)
        chart_out_checks = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}

        for project_id, project_name, project_type in projects:
            summary = {
                'project_id': project_id,
                'project_name': project_name,
                'project_type': project_type
            }

            # Daire sayısı
            cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s", (project_id,))
            summary['total_flats'] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s AND owner_id IS NOT NULL", (project_id,))
            summary['assigned_flats'] = cur.fetchone()[0]

            # --- Giderler ---
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
            total_large_expenses = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
            total_petty_cash = cur.fetchone()[0]
            total_expenses = total_large_expenses + total_petty_cash

            # Ödenmiş giderler (planlı + küçük)
            cur.execute("""
                SELECT COALESCE(SUM(es.paid_amount), 0)
                FROM expense_schedule es
                JOIN expenses e ON es.expense_id = e.id
                WHERE e.project_id = %s
            """, (project_id,))
            paid_large_expenses = cur.fetchone()[0]
            cur.execute("""
                SELECT COALESCE(SUM(amount), 0)
                FROM petty_cash_expenses
                WHERE project_id = %s
            """, (project_id,))
            paid_petty_expenses = cur.fetchone()[0]
            paid_expenses = paid_large_expenses + paid_petty_expenses
            remaining_expenses = total_expenses - paid_expenses
            progress_percentage_expenses = (paid_expenses / total_expenses * 100) if total_expenses > 0 else 0

            if project_type == 'normal':
                # --- GELİRLER ---
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    WHERE f.project_id = %s
                """, (project_id,))
                planned_revenue = cur.fetchone()[0]

                # GÜNCELLENMİŞ TAHSİLAT SORGUSU
                cur.execute("""
                    SELECT COALESCE(SUM(p.amount), 0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    LEFT JOIN checks c ON p.check_id = c.id
                    WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
                """, (project_id,))
                collected_revenue = cur.fetchone()[0]

                remaining_revenue = planned_revenue - collected_revenue

                # --- Ek İstatistikler ---
                cur.execute("""
                    SELECT COUNT(*) FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    WHERE f.project_id = %s AND s.is_paid = TRUE
                """, (project_id,))
                paid_installments = cur.fetchone()[0]
                cur.execute("""
                    SELECT COUNT(*) FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    WHERE f.project_id = %s AND s.is_paid = FALSE
                """, (project_id,))
                unpaid_installments = cur.fetchone()[0]

                progress_percentage = (collected_revenue / planned_revenue * 100) if planned_revenue > 0 else 0

                net_cash_flow = collected_revenue - paid_expenses

                summary.update({
                    'planned_revenue': planned_revenue,
                    'collected_revenue': collected_revenue,
                    'remaining_revenue': remaining_revenue,
                    'paid_expenses': paid_expenses,
                    'remaining_expenses': remaining_expenses,
                    'total_expenses': total_expenses,
                    'net_cash_flow': net_cash_flow,
                    'progress_percentage': progress_percentage,
                    'progress_percentage_expenses': progress_percentage_expenses,
                    'paid_installments': paid_installments,
                    'unpaid_installments': unpaid_installments
                })

                # --- 12 month income and expense (from the grouped queries) ---
                # float() on each part, then the sum: the same arithmetic the
                # per month queries did, so the floats come out identical.
                labels = [m.strftime("%b %Y") for m in chart_months]
                income_series, expense_series = [], []
                for m in chart_months:
                    income_series.append(float(chart_income.get((project_id, m), 0)))
                    paid_large = float(chart_large.get((project_id, m), 0))
                    petty_paid = float(chart_petty.get((project_id, m), 0))
                    expense_series.append(paid_large + petty_paid)

                net_series = [inc - exp for inc, exp in zip(income_series, expense_series)]
                monthly_series[project_id] = {
                    'labels': labels,
                    'income': income_series,
                    'expense': expense_series,
                    'net': net_series
                }

                # --- 12 month check status (from the grouped queries) ---
                def check_line(sums, status):
                    return [float(sums.get((project_id, status, m), 0))
                            for m in chart_months]

                check_series[project_id] = {
                    'labels': labels,
                    'incoming_portfolio': check_line(chart_in_checks, 'portfoyde'),
                    'incoming_cleared': check_line(chart_in_checks, 'tahsil_edildi'),
                    'outgoing_given': check_line(chart_out_checks, 'verildi'),
                    'outgoing_paid': check_line(chart_out_checks, 'odendi')
                }

                # --- Bu ay kutuları ---
                month_end = add_months(start_month, 1)
                cur.execute(
                    """
                    SELECT COALESCE(SUM(p.amount), 0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    LEFT JOIN checks c ON p.check_id = c.id
                    WHERE f.project_id = %s
                      AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
                      AND p.payment_date >= %s AND p.payment_date < %s
                    """,
                    (project_id, start_month, month_end)
                )
                month_income = float(cur.fetchone()[0])

                cur.execute(
                    """
                    SELECT COALESCE(SUM(sp.amount), 0)
                    FROM supplier_payments sp
                    JOIN expenses e ON sp.expense_id = e.id
                    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
                    WHERE e.project_id = %s
                      AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
                      AND sp.payment_date >= %s AND sp.payment_date < %s
                    """,
                    (project_id, start_month, month_end)
                )
                month_expense_large = float(cur.fetchone()[0])

                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0)
                    FROM petty_cash_expenses
                    WHERE project_id = %s AND expense_date >= %s AND expense_date < %s
                    """,
                    (project_id, start_month, month_end)
                )
                month_expense_petty = float(cur.fetchone()[0])
                month_expense = month_expense_large + month_expense_petty

                cur.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(s.amount - s.paid_amount),0)
                    FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    WHERE f.project_id = %s AND s.is_paid = FALSE AND s.due_date < %s
                    """,
                    (project_id, today)
                )
                overdue_inst_count, overdue_inst_amount = cur.fetchone()
                overdue_inst_amount = float(overdue_inst_amount)

                cur.execute(
                    """
                    SELECT COALESCE(SUM(c.amount),0)
                    FROM checks c
                    JOIN payments p ON c.id = p.check_id
                    JOIN flats f ON p.flat_id = f.id
                    WHERE f.project_id = %s AND c.status = 'portfoyde' AND c.due_date < %s
                    """,
                    (project_id, today)
                )
                overdue_checks_amount = float(cur.fetchone()[0])

                month_boxes[project_id] = {
                    'month_income': month_income,
                    'month_expense': month_expense,
                    'month_net': month_income - month_expense,
                    'overdue_inst_count': int(overdue_inst_count),
                    'overdue_inst_amount': overdue_inst_amount,
                    'overdue_checks_amount': overdue_checks_amount
                }

                # --- Geciken taksit listesi (en eski 10) ---
                cur.execute(
                    """
                    SELECT s.due_date, c.first_name || ' ' || c.last_name AS cust,
                           f.block_name, f.floor, f.flat_no,
                           s.amount - s.paid_amount AS kalan
                    FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    JOIN customers c ON f.owner_id = c.id
                    WHERE f.project_id = %s AND s.is_paid = FALSE AND s.due_date < %s
                    ORDER BY s.due_date ASC
                    LIMIT 10
                    """,
                    (project_id, today)
                )
                rows = cur.fetchall()
                overdue_items[project_id] = {
                    'rows': rows,
                    'total': float(sum(r[5] for r in rows))
                }

                # --- Kasa projeksiyonu (90 gün, 30'ar gün) ---
                cur.execute(
                    """
                    SELECT COALESCE(SUM(p.amount),0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    LEFT JOIN checks c ON p.check_id = c.id
                    WHERE f.project_id = %s
                      AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
                      AND p.payment_date >= %s - INTERVAL '90 days' AND p.payment_date < %s
                    """,
                    (project_id, today, today)
                )
                last_income = float(cur.fetchone()[0])

                cur.execute(
                    """
                    SELECT COALESCE(SUM(sp.amount),0)
                    FROM supplier_payments sp
                    JOIN expenses e ON sp.expense_id = e.id
                    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
                    WHERE e.project_id = %s
                      AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
                      AND sp.payment_date >= %s - INTERVAL '90 days' AND sp.payment_date < %s
                    """,
                    (project_id, today, today)
                )
                last_expense_large = float(cur.fetchone()[0])

                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount),0)
                    FROM petty_cash_expenses
                    WHERE project_id = %s
                      AND expense_date >= %s - INTERVAL '90 days' AND expense_date < %s
                    """,
                    (project_id, today, today)
                )
                last_expense_petty = float(cur.fetchone()[0])
                last_expense = last_expense_large + last_expense_petty

                avg_daily = (last_income - last_expense) / 90.0

                cur.execute(
                    """
                    SELECT due_date, COALESCE(SUM(amount - paid_amount),0) AS rem
                    FROM installment_schedule s
                    JOIN flats f ON s.flat_id = f.id
                    WHERE f.project_id = %s AND s.is_paid = FALSE AND s.due_date > %s AND s.due_date <= %s + INTERVAL '90 days'
                    GROUP BY due_date
                    """,
                    (project_id, today, today)
                )
                future_income = [(d, float(v)) for d, v in cur.fetchall()]

                cur.execute(
                    """
                    SELECT es.due_date, COALESCE(SUM(es.amount - es.paid_amount),0) AS rem
                    FROM expense_schedule es
                    JOIN expenses e ON es.expense_id = e.id
                    WHERE e.project_id = %s AND es.is_paid = FALSE AND es.due_date > %s AND es.due_date <= %s + INTERVAL '90 days'
                    GROUP BY es.due_date
                    """,
                    (project_id, today, today)
                )
                future_expense = [(d, float(v)) for d, v in cur.fetchall()]

                current_cash = float(net_cash_flow)
                points = []
                for days_ahead in (0, 30, 60, 90):
                    target = today + relativedelta(days=days_ahead)
                    drift = avg_daily * days_ahead
                    planned_in = sum(val for d, val in future_income if d <= target)
                    planned_out = sum(val for d, val in future_expense if d <= target)
                    forecast = current_cash + drift + planned_in - planned_out
                    points.append({'date': target.strftime("%Y-%m-%d"), 'cash': forecast})

                projection_series[project_id] = points

            elif project_type == 'cooperative':
                cur.execute("""
                    SELECT COALESCE(SUM(p.amount), 0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    LEFT JOIN checks c ON p.check_id = c.id
                    WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
                """, (project_id,))
                member_contributions = cur.fetchone()[0]

                cash_balance = member_contributions - paid_expenses

                summary.update({
                    'member_contributions': member_contributions,
                    'paid_expenses': paid_expenses,
                    'remaining_expenses': remaining_expenses,
                    'total_expenses': total_expenses,
                    'cash_balance': cash_balance
                })

            project_summaries.append(summary)

    except Exception:
        app.logger.exception('Failed to build the reports page')
        flash("Raporlar oluşturulurken bir hata oluştu. Rapor eksik olabilir.",
              "danger")
    finally:
        cur.close()
        conn.close()

    return render_template('reports.html',
                           project_summaries=project_summaries,
                           monthly_series=monthly_series,
                           check_series=check_series,
                           projection_series=projection_series,
                           overdue_items=overdue_items,
                           month_boxes=month_boxes,
                           user_name=session.get('user_name'))




# dashboard fonksiyonu
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_connection()
    cur = conn.cursor()
    today = date.today()
    
    try:
        # --- Genel İstatistikler ---
        cur.execute("SELECT COUNT(*) FROM customers;")
        total_customers = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM flats;")
        total_flats = cur.fetchone()[0]
        
        # --- Proje Listesi ---
        cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
        projects_raw = cur.fetchall()
        projects = [{'id': p[0], 'name': p[1], 'project_type': p[2]} for p in projects_raw]

        # --- Müşteri Ödemeleri (Proje ve Daire Detayları Eklendi) ---
        customer_payments_sql = """
            SELECT 
                c.first_name, c.last_name, s.due_date, 
                s.amount - s.paid_amount as remaining_due,
                p.name as project_name, f.block_name, f.floor, f.flat_no
            FROM installment_schedule s 
            JOIN flats f ON s.flat_id = f.id 
            JOIN customers c ON f.owner_id = c.id
            JOIN projects p ON f.project_id = p.id
            WHERE s.is_paid = FALSE AND s.due_date {condition}
            ORDER BY s.due_date ASC
        """
        cur.execute(customer_payments_sql.format(condition="< %s"), (today,))
        overdue_customer_payments = cur.fetchall()

        cur.execute(customer_payments_sql.format(condition="BETWEEN %s AND %s + INTERVAL '7 days'"), (today, today))
        upcoming_customer_payments = cur.fetchall()

        # --- Gider Ödemeleri ---
        cur.execute("""
            SELECT s.name, es.due_date, es.amount - es.paid_amount as remaining_due, p.name as project_name
            FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id JOIN suppliers s ON e.supplier_id = s.id JOIN projects p ON e.project_id = p.id
            WHERE es.is_paid = FALSE AND es.due_date < %s ORDER BY es.due_date ASC
        """, (today,))
        overdue_expense_payments = cur.fetchall()

        cur.execute("""
            SELECT s.name, es.due_date, es.amount - es.paid_amount as remaining_due, p.name as project_name
            FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id JOIN suppliers s ON e.supplier_id = s.id JOIN projects p ON e.project_id = p.id
            WHERE es.is_paid = FALSE AND es.due_date BETWEEN %s AND %s + INTERVAL '7 days' ORDER BY es.due_date ASC
        """, (today, today))
        upcoming_expense_payments = cur.fetchall()
        
        # --- Çek Özetleri (Önümüzdeki 30 gün) ---
        cur.execute("""
            SELECT c.due_date, c.amount, cus.first_name || ' ' || cus.last_name AS customer_name 
            FROM checks c JOIN customers cus ON c.customer_id = cus.id
            WHERE c.status = 'portfoyde' AND c.due_date BETWEEN %s AND %s + INTERVAL '30 days' 
            ORDER BY c.due_date ASC
        """, (today, today))
        upcoming_incoming_checks = cur.fetchall()

        cur.execute("""
            SELECT oc.due_date, oc.amount, s.name AS supplier_name 
            FROM outgoing_checks oc JOIN suppliers s ON oc.supplier_id = s.id 
            WHERE oc.status = 'verildi' AND oc.due_date BETWEEN %s AND %s + INTERVAL '30 days' 
            ORDER BY oc.due_date ASC
        """, (today, today))
        upcoming_outgoing_checks = cur.fetchall()

        # --- Bu Ayın Nakit Akışı ---
        cur.execute("SELECT COALESCE(SUM(amount - paid_amount), 0) FROM installment_schedule WHERE is_paid = FALSE AND DATE_TRUNC('month', due_date) = DATE_TRUNC('month', CURRENT_DATE)")
        monthly_income = cur.fetchone()[0]
        
        cur.execute("SELECT COALESCE(SUM(amount - paid_amount), 0) FROM expense_schedule WHERE is_paid = FALSE AND DATE_TRUNC('month', due_date) = DATE_TRUNC('month', CURRENT_DATE)")
        monthly_scheduled_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE DATE_TRUNC('month', expense_date) = DATE_TRUNC('month', CURRENT_DATE)")
        monthly_petty_cash_expense = cur.fetchone()[0]
        monthly_expense = monthly_scheduled_expense + monthly_petty_cash_expense

        monthly_cash_flow = {'income': monthly_income, 'expense': monthly_expense, 'net': monthly_income - monthly_expense}

    except Exception:
        app.logger.exception('Failed to build the dashboard')
        flash("Ana sayfa yüklenirken bir hata oluştu. Bazı rakamlar eksik "
              "olabilir.", "danger")
        total_customers, total_flats = 0, 0
        projects, overdue_customer_payments, upcoming_customer_payments, overdue_expense_payments, upcoming_expense_payments, upcoming_incoming_checks, upcoming_outgoing_checks = [], [], [], [], [], [], []
        monthly_cash_flow = {'income': 0, 'expense': 0, 'net': 0}
    finally:
        cur.close()
        conn.close()

    return render_template('dashboard.html',
        user_name=session.get('user_name'),
        total_customers=total_customers, total_flats=total_flats,
        projects=projects,
        monthly_cash_flow=monthly_cash_flow,
        overdue_customer_payments=overdue_customer_payments,
        upcoming_customer_payments=upcoming_customer_payments,
        overdue_expense_payments=overdue_expense_payments,
        upcoming_expense_payments=upcoming_expense_payments,
        upcoming_incoming_checks=upcoming_incoming_checks,
        upcoming_outgoing_checks=upcoming_outgoing_checks
    )



@app.route('/api/monthly_payments')
@json_login_required({'error': 'Yetkisiz erişim'})
def monthly_payments_api():
    """Son 12 ayın aylık toplam ödemelerini JSON formatında döndürür."""
    conn = get_connection()
    cur = conn.cursor()
    try:

        # Son 12 ayın verisini çekmek için veritabanına özel bir sorgu gönder
        # Bu sorgu, her ayın başlangıcını ve o aydaki toplam ödemeyi hesaplar.
        # `DATE_TRUNC('month', ...)` fonksiyonu tarihi ayın ilk gününe yuvarlar
        # Only cash and cleared checks are real money. A check in the portfolio
        # or a bounced one must not appear as collected income on the chart.
        cur.execute("""
            SELECT 
                DATE_TRUNC('month', p.payment_date)::DATE AS month, 
                SUM(p.amount) AS total_amount
            FROM 
                payments p
                LEFT JOIN checks c ON p.check_id = c.id
            WHERE 
                p.payment_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months'
                AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
            GROUP BY 
                month
            ORDER BY 
                month;
        """)
    
        results = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    # Veritabanından gelen veriyi grafiğin beklediği formata dönüştür
    labels = []
    data = []
    today = date.today()
    
    db_data = {row[0]: float(row[1]) for row in results}

    for i in range(12):
        month_date = (today - relativedelta(months=11 - i))
        month_start = month_date.replace(day=1)

        labels.append(month_start.isoformat()) 
        
        data.append(db_data.get(month_start, 0))

    return jsonify({'labels': labels, 'data': data})
