"""Incoming and outgoing checks, and changing their status."""
from flask import render_template, request, redirect, url_for, session, flash
from db import get_connection
from datetime import date, datetime
from decimal import Decimal
from core import app
from helpers import (
    log_audit, get_page_number, page_window, build_pager, split_page,
    safe_next, login_required,
)
from reconcile import reconcile_supplier_payments, reconcile_customer_payments


@app.route('/checks')
@login_required
def list_checks():
    conn = get_connection()
    cur = conn.cursor()
    
    # --- YENİ: Daha detaylı özet için değişkenler ---
    total_incoming_portfolio = Decimal(0)
    total_outgoing_portfolio = Decimal(0)
    total_incoming_cleared = Decimal(0)
    total_outgoing_paid = Decimal(0)
    net_incoming_checks = Decimal(0)
    net_outgoing_checks = Decimal(0)

    # Hata durumunda boş dönmeleri için burada tanımlıyoruz
    incoming_checks, outgoing_checks = [], []
    incoming_parties, outgoing_parties = [], []

    # The two lists page independently, so they need a parameter each.
    in_page = get_page_number('in_page')
    out_page = get_page_number('out_page')
    incoming_pager = build_pager(in_page, has_next=False, arg_name='in_page')
    outgoing_pager = build_pager(out_page, has_next=False, arg_name='out_page')
    
    in_due_from = request.args.get('in_due_from')
    in_due_to = request.args.get('in_due_to')
    in_customer = request.args.get('in_customer', '').strip()
    in_status = request.args.get('in_status', 'all')
    out_due_from = request.args.get('out_due_from')
    out_due_to = request.args.get('out_due_to')
    out_supplier = request.args.get('out_supplier', '').strip()
    out_status = request.args.get('out_status', 'all')

    try:
        # Alınan çekler (toplam ve seçenekler için filtresiz)
        cur.execute("""
            SELECT 
                c.id, c.due_date, c.amount, cus.first_name || ' ' || cus.last_name AS customer_name,
                c.bank_name, c.check_number, c.status
            FROM checks c
            LEFT JOIN customers cus ON c.customer_id = cus.id
            ORDER BY c.due_date ASC
        """)
        incoming_all = cur.fetchall()
        for check in incoming_all:
            if check[6] == 'portfoyde':
                total_incoming_portfolio += check[2]
            if check[6] == 'tahsil_edildi':
                total_incoming_cleared += check[2]
        incoming_parties = sorted({c[3] for c in incoming_all if c[3]})

        # Filtreli alınan çekler
        in_params = []
        in_sql = """
            SELECT 
                c.id, c.due_date, c.amount, cus.first_name || ' ' || cus.last_name AS customer_name,
                c.bank_name, c.check_number, c.status
            FROM checks c
            LEFT JOIN customers cus ON c.customer_id = cus.id
            WHERE 1=1
        """
        if in_due_from:
            in_sql += " AND c.due_date >= %s"
            in_params.append(datetime.strptime(in_due_from, '%Y-%m-%d').date())
        if in_due_to:
            in_sql += " AND c.due_date <= %s"
            in_params.append(datetime.strptime(in_due_to, '%Y-%m-%d').date())
        if in_customer:
            in_sql += " AND (cus.first_name || ' ' || cus.last_name) ILIKE %s"
            in_params.append(f"%{in_customer}%")
        if in_status != 'all':
            in_sql += " AND c.status = %s"
            in_params.append(in_status)
        in_sql += " ORDER BY c.due_date ASC"
        in_limit, in_offset = page_window(in_page)
        in_sql += " LIMIT %s OFFSET %s"
        in_params.extend([in_limit, in_offset])
        cur.execute(in_sql, tuple(in_params))
        incoming_checks, incoming_pager = split_page(cur.fetchall(), in_page,
                                                     'in_page')

        # Verilen Çekleri Çek (Tedarikçilere)
        cur.execute("""
            SELECT 
                oc.id, oc.due_date, oc.amount, s.name AS supplier_name,
                oc.bank_name, oc.check_number, oc.status
            FROM outgoing_checks oc
            LEFT JOIN suppliers s ON oc.supplier_id = s.id
            ORDER BY oc.due_date ASC
        """)
        outgoing_all = cur.fetchall()
        for check in outgoing_all:
            if check[6] == 'verildi':
                total_outgoing_portfolio += check[2]
            if check[6] == 'odendi':
                total_outgoing_paid += check[2]
        outgoing_parties = sorted({c[3] for c in outgoing_all if c[3]})

        out_params = []
        out_sql = """
            SELECT 
                oc.id, oc.due_date, oc.amount, s.name AS supplier_name,
                oc.bank_name, oc.check_number, oc.status
            FROM outgoing_checks oc
            LEFT JOIN suppliers s ON oc.supplier_id = s.id
            WHERE 1=1
        """
        if out_due_from:
            out_sql += " AND oc.due_date >= %s"
            out_params.append(datetime.strptime(out_due_from, '%Y-%m-%d').date())
        if out_due_to:
            out_sql += " AND oc.due_date <= %s"
            out_params.append(datetime.strptime(out_due_to, '%Y-%m-%d').date())
        if out_supplier:
            out_sql += " AND s.name ILIKE %s"
            out_params.append(f"%{out_supplier}%")
        if out_status != 'all':
            out_sql += " AND oc.status = %s"
            out_params.append(out_status)
        out_sql += " ORDER BY oc.due_date ASC"
        out_limit, out_offset = page_window(out_page)
        out_sql += " LIMIT %s OFFSET %s"
        out_params.extend([out_limit, out_offset])
        cur.execute(out_sql, tuple(out_params))
        outgoing_checks, outgoing_pager = split_page(cur.fetchall(), out_page,
                                                     'out_page')

    except Exception:
        app.logger.exception('Failed to list checks')
        flash("Çekler listelenirken bir hata oluştu. Liste eksik olabilir.",
              "danger")
    finally:
        cur.close()
        conn.close()

    # Net çek pozisyonunu hesapla
    net_check_position = total_incoming_portfolio - total_outgoing_portfolio
    net_incoming_checks = total_incoming_portfolio - total_incoming_cleared
    net_outgoing_checks = total_outgoing_portfolio - total_outgoing_paid

    return render_template('checks.html', 
                           incoming_checks=incoming_checks,
                           outgoing_checks=outgoing_checks,
                           user_name=session.get('user_name'),
                           today=date.today(),
                           # --- YENİ: Hesaplanan toplamları şablona gönder ---
                           total_incoming_portfolio=total_incoming_portfolio,
                           total_incoming_cleared=total_incoming_cleared,
                           total_outgoing_portfolio=total_outgoing_portfolio,
                           total_outgoing_paid=total_outgoing_paid,
                           net_incoming_checks=net_incoming_checks,
                           net_outgoing_checks=net_outgoing_checks,
                           net_check_position=net_check_position,
                           in_due_from=in_due_from, in_due_to=in_due_to, in_customer=in_customer,
                           in_status=in_status,
                           out_due_from=out_due_from, out_due_to=out_due_to, out_supplier=out_supplier,
                           out_status=out_status,
                           incoming_parties=incoming_parties, outgoing_parties=outgoing_parties,
                           incoming_pager=incoming_pager, outgoing_pager=outgoing_pager
                           )


# update_check_status fonksiyonu
@app.route('/check/update_status', methods=['POST'])
@login_required
def update_check_status():
    check_id = request.form.get('check_id')
    check_type = request.form.get('check_type') 
    new_status = request.form.get('new_status')

    conn = get_connection()
    cur = conn.cursor()

    try:
        if check_type == 'incoming':
            # 1. Çek durumunu güncelle
            cur.execute("UPDATE checks SET status = %s WHERE id = %s", (new_status, check_id))
            
            # 2. Bu çeke bağlı olan daireyi (flat_id) bul
            cur.execute("SELECT flat_id FROM payments WHERE check_id = %s", (check_id,))
            payment_record = cur.fetchone()
            
            if payment_record:
                # 3. Dairenin tüm taksitlerini GEÇERLİ ödemelere göre yeniden hesapla
                reconcile_customer_payments(cur, payment_record[0])
                flash('Müşteri çeki durumu güncellendi ve borca yansıtıldı.', 'success')

        elif check_type == 'outgoing':
            # 1. Firma çeki durumunu güncelle
            cur.execute("UPDATE outgoing_checks SET status = %s WHERE id = %s", (new_status, check_id))
            
            # 2. Bu çeke bağlı olan giderleri (expense_id) bul
            # (Bir çek bazen birden fazla gider kaydı için verilmiş olabilir)
            cur.execute("SELECT DISTINCT expense_id FROM supplier_payments WHERE check_id = %s", (check_id,))
            expense_records = cur.fetchall()
            
            for (expense_id,) in expense_records:
                # 3. Giderin tüm taksitlerini GEÇERLİ ödemelere göre yeniden hesapla
                reconcile_supplier_payments(cur, expense_id)
            
            flash('Firma çeki durumu güncellendi ve gider bakiyesine yansıtıldı.', 'success')

        # Audit log
        log_audit(
            cur,
            session.get('user_id'),
            'check_status_update',
            'incoming_check' if check_type == 'incoming' else 'outgoing_check',
            check_id,
            {'new_status': new_status, 'check_type': check_type}
        )

        conn.commit()

    except Exception:
        conn.rollback()
        app.logger.exception('Failed to update status of check %s',
                             request.form.get('check_id'))
        flash("Çek durumu güncellenirken bir hata oluştu. Çekin durumu "
              "değişmedi.", "danger")
    finally:
        cur.close()
        conn.close()

    next_url = (safe_next(request.form.get('next'))
                or safe_next(request.referrer) or url_for('debt_status'))
    return redirect(next_url)
