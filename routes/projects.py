"""Projects and their flats: create, edit, delete, the overview and the
transaction list of one project."""
from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from db import get_connection
from datetime import date, datetime
from decimal import Decimal
from core import app
from helpers import (
    log_audit, claim_submission_token, login_required, json_login_required,
    repair_out_of_range_dates,
)


@app.route('/project/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        project_type = request.form['project_type']
        floors = request.form['floors']
        flats = request.form['flats']

        conn = get_connection()
        cur = conn.cursor()
        try:

            # Double submit guard, in the same transaction as the insert below.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'new_project'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(url_for('dashboard'))

            cur.execute("""
                INSERT INTO projects (name, address, project_type, total_floors, total_flats)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (name, address, project_type, floors, flats))
            project_id = cur.fetchone()[0]  # Proje ID'yi al
            log_audit(cur, session.get('user_id'), 'project_create', 'project', project_id,
                      {'name': name, 'type': project_type, 'floors': floors, 'flats': flats})
            conn.commit()

            flash('Proje başarıyla eklendi. Şimdi daireleri tanımlayabilirsiniz.', 'success')
            return redirect(url_for('manage_flats', project_id=project_id)) # YENİ YÖNLENDİRME
        finally:
            cur.close()
            conn.close()

    return render_template('project_new.html')



@app.route('/project/<int:project_id>/manage_flats', methods=['GET', 'POST'])
@login_required
def manage_flats(project_id):
    """
    Bir projedeki daireleri akıllıca yönetir (ekler, günceller, sahibi olmayanları siler).
    Mevcut ve satılmış daireleri korur.
    """
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot create the flats twice.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'manage_flats'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(url_for('manage_flats', project_id=project_id))

            # Formdan gelen tüm daire verilerini listeler halinde al
            flat_ids = request.form.getlist('flat_id[]')
            block_names = request.form.getlist('block_name[]')
            flat_nos = request.form.getlist('flat_no[]')
            floors = request.form.getlist('floor[]')
            room_types = request.form.getlist('room_type[]')

            # Veritabanındaki mevcut daire ID'lerini al (sadece sahibi olmayanları sileceğiz)
            cur.execute("SELECT id FROM flats WHERE project_id = %s AND owner_id IS NULL", (project_id,))
            deletable_ids_in_db = {row[0] for row in cur.fetchall()}

            submitted_ids = set()

            for i in range(len(block_names)):
                # Sadece dolu satırları işle
                if block_names[i] and flat_nos[i] and floors[i] and room_types[i]:
                    flat_id = flat_ids[i]
                    
                    if flat_id and flat_id != 'new': # Mevcut bir daire ise GÜNCELLE
                        flat_id = int(flat_id)
                        submitted_ids.add(flat_id)
                        cur.execute("""
                            UPDATE flats SET block_name=%s, flat_no=%s, floor=%s, room_type=%s
                            WHERE id=%s
                        """, (block_names[i], flat_nos[i], floors[i], room_types[i], flat_id))
                    
                    elif flat_id == 'new': # Yeni bir daire ise EKLE
                        cur.execute("""
                            INSERT INTO flats (project_id, block_name, flat_no, floor, room_type)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (project_id, block_names[i], flat_nos[i], floors[i], room_types[i]))

            # Formdan silinmiş olan (ama veritabanında olan) boş daireleri SİL
            ids_to_delete = deletable_ids_in_db - submitted_ids
            if ids_to_delete:
                # %s'nin tuple olarak formatlanması için (id,) şeklinde kullanıyoruz
                for single_id in ids_to_delete:
                    cur.execute("DELETE FROM flats WHERE id = %s", (single_id,))

            conn.commit()
            flash('Daire listesi başarıyla güncellendi.', 'success')
            return redirect(url_for('assign_flat_owner'))

        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update flats of project %s', project_id)
            flash('Daireler güncellenirken bir hata oluştu. Değişiklikler '
                  'kaydedilmedi.', 'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('manage_flats', project_id=project_id))

    # GET isteği için
    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name = cur.fetchone()[0]
        
        # Mevcut daireleri ve sahip durumlarını çek
        cur.execute("SELECT id, block_name, flat_no, floor, room_type, owner_id FROM flats WHERE project_id = %s ORDER BY block_name, floor, flat_no", (project_id,))
        existing_flats = cur.fetchall()
        
    except Exception:
        app.logger.exception('Failed to load flats page of project %s', project_id)
        flash('Daire bilgileri alınırken bir hata oluştu. Liste eksik olabilir.',
              'danger')
        project_name = "Bilinmeyen Proje"
        existing_flats = []
    finally:
        cur.close()
        conn.close()

    return render_template('manage_flats.html', 
                           project_id=project_id, 
                           project_name=project_name,
                           existing_flats=existing_flats)

@app.route('/project/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    """Mevcut bir projeyi düzenler ve daire sayısı artarsa daire ekleme sayfasına yönlendirir."""
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        project_type = request.form['project_type']
        total_floors = request.form['total_floors']
        total_flats = request.form['total_flats']
        try:
            cur.execute("""
                UPDATE projects
                SET name = %s, address = %s, project_type = %s, total_floors = %s, total_flats = %s
                WHERE id = %s
            """, (name, address, project_type, total_floors, total_flats, project_id))
            conn.commit()
            
            flash('Proje başarıyla güncellendi. Şimdi daire bilgilerini gözden geçirebilirsiniz.', 'success')
            return redirect(url_for('manage_flats', project_id=project_id)) # YENİ YÖNLENDİRME

        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update project %s', project_id)
            flash('Proje güncellenirken bir hata oluştu. Değişiklikler '
                  'kaydedilmedi.', 'danger')
            return redirect(url_for('edit_project', project_id=project_id))
        finally:
            cur.close()
            conn.close()

    # GET isteği için proje verilerini çek
    cur.execute("SELECT id, name, address, project_type, total_floors, total_flats FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    cur.close()
    conn.close()

    if project is None:
        flash('Düzenlenecek proje bulunamadı.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('edit_project.html', project=project, user_name=session.get('user_name'))

@app.route('/project/<int:project_id>/delete', methods=['POST'])
@login_required
def delete_project(project_id):
    """Bir projeyi ve ona bağlı tüm verileri (ilişkili tüm çekler dahil) siler."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        proj_row = cur.fetchone()
        proj_name = proj_row[0] if proj_row else None

        # 1. Projeye bağlı GELİR çeklerini bul (payments -> checks)
        cur.execute("""
            SELECT p.check_id FROM payments p
            JOIN flats f ON p.flat_id = f.id
            WHERE f.project_id = %s AND p.check_id IS NOT NULL
        """, (project_id,))
        incoming_check_ids = [row[0] for row in cur.fetchall()]

        # 2. Projeye bağlı GİDER çeklerini bul (expenses -> outgoing_checks)
        cur.execute("""
            SELECT e.outgoing_check_id FROM expenses e
            WHERE e.project_id = %s AND e.outgoing_check_id IS NOT NULL
        """, (project_id,))
        outgoing_check_ids = [row[0] for row in cur.fetchall()]

        # 3. Önce Projenin kendisini sil. Veritabanındaki `ON DELETE CASCADE` ayarı,
        # projeye bağlı flats, payments, expenses, installment_schedule, expense_schedule gibi
        # tüm alt kayıtları otomatik olarak silecektir.
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        
        # 4. Artık güvende olan (ana kayıtları silinmiş) gelir çeklerini sil
        if incoming_check_ids:
            cur.execute("DELETE FROM checks WHERE id IN %s", (tuple(incoming_check_ids),))

        # 5. Artık güvende olan gider çeklerini sil
        if outgoing_check_ids:
            cur.execute("DELETE FROM outgoing_checks WHERE id IN %s", (tuple(outgoing_check_ids),))
        
        # 6. Audit log
        log_audit(
            cur,
            session.get('user_id'),
            'project_delete',
            'project',
            project_id,
            {'name': proj_name, 'incoming_checks': incoming_check_ids, 'outgoing_checks': outgoing_check_ids}
        )

        conn.commit()
        flash('Proje ve ilgili tüm veriler (çekler dahil) başarıyla silindi.', 'success')
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to delete project %s', project_id)
        flash('Proje silinirken bir hata oluştu. Proje silinmedi, tüm '
              'veriler korundu.', 'danger')
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('dashboard'))


# app.py'deki mevcut project_transactions fonksiyonunu bu kodla değiştirin

@app.route('/project/<int:project_id>/transactions', methods=['GET'])
@login_required
def project_transactions(project_id):
    conn = get_connection()
    cur = conn.cursor()

    view_type = request.args.get('view', 'all')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    income_party = (request.args.get('income_party') or '').strip()
    income_method = request.args.get('income_method', 'all')
    income_status = request.args.get('income_status', 'all')
    expense_party = (request.args.get('expense_party') or '').strip()
    expense_method = request.args.get('expense_method', 'all')
    expense_status = request.args.get('expense_status', 'all')
    income_data, expense_data = [], []
    income_parties, expense_parties = [], []
    project_name = "Bilinmiyor"
    
    total_realized_income = Decimal(0)
    total_realized_expense = Decimal(0)

    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name = cur.fetchone()[0]

        # Gerçekleşen Toplam Gelir (Sadece nakitler ve 'tahsil_edildi' durumundaki çekler)
        cur.execute("""
            SELECT COALESCE(SUM(p.amount), 0)
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN checks chk ON p.check_id = chk.id
            WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR chk.status = 'tahsil_edildi')
        """, (project_id,))
        total_realized_income = cur.fetchone()[0]

        # Gerçekleşen Toplam Gider (Sadece nakitler ve 'odendi' durumundaki çekler)
        cur.execute("""
            SELECT COALESCE(SUM(sp.amount), 0)
            FROM supplier_payments sp
            JOIN expenses e ON sp.expense_id = e.id
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
            WHERE e.project_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
        """, (project_id,))
        realized_large_expense = cur.fetchone()[0]
        
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        realized_petty_cash = cur.fetchone()[0]
        
        total_realized_expense = (realized_large_expense or 0) + (realized_petty_cash or 0)

        # Gerçekleşen Gelir Listesi
        if view_type in ['all', 'income']:
            cur.execute("""
                SELECT p.payment_date, p.description, p.amount, p.payment_method,
                       c.first_name, c.last_name, f.block_name, f.floor, f.flat_no
                FROM payments p
                JOIN flats f ON p.flat_id = f.id
                LEFT JOIN customers c ON f.owner_id = c.id
                LEFT JOIN checks chk ON p.check_id = chk.id
                WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR chk.status = 'tahsil_edildi')
                ORDER BY p.payment_date DESC
            """, (project_id,))
            income_rows = cur.fetchall()
            for date_v, desc, amount, method, first, last, block, floor, flat_no in income_rows:
                status = 'tahsil_edildi' if method == 'çek' else 'ödendi'
                income_data.append({
                    'date': date_v,
                    'desc': desc,
                    'amount': amount,
                    'method': method,
                    'party': f"{first or ''} {last or ''}".strip(),
                    'details': f"Blok: {block or 'N/A'}, Kat: {floor}, No: {flat_no}",
                    'status': status
                })

        # Gerçekleşen Gider Listesi
        if view_type in ['all', 'expense']:
            cur.execute("""
                SELECT sp.payment_date, e.title, s.name, sp.description, sp.payment_method, sp.amount, 'Büyük Gider'
                FROM supplier_payments sp
                JOIN expenses e ON sp.expense_id = e.id
                LEFT JOIN suppliers s ON e.supplier_id = s.id
                LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
                WHERE e.project_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
                UNION ALL
                SELECT pce.expense_date, pce.title, 'Kasa', pce.description, 'nakit', pce.amount, 'Küçük Gider'
                FROM petty_cash_expenses pce
                WHERE pce.project_id = %s
                ORDER BY 1 DESC
            """, (project_id, project_id))
            exp_rows = cur.fetchall()
            for date_v, title, party_name, desc, method, amount, typ in exp_rows:
                status = 'odendi'
                expense_data.append({
                    'date': date_v,
                    'title': title,
                    'party': party_name,
                    'description': desc,
                    'method': method,
                    'amount': amount,
                    'type': typ,
                    'status': status
                })

        # Tarih filtresi
        if start_date_str:
            sd = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            income_data = [i for i in income_data if i['date'] >= sd]
            expense_data = [e for e in expense_data if e['date'] >= sd]
        if end_date_str:
            ed = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            income_data = [i for i in income_data if i['date'] <= ed]
            expense_data = [e for e in expense_data if e['date'] <= ed]

        # Party listeleri (filtre sonrası)
        income_parties = sorted({i['party'] for i in income_data})
        expense_parties = sorted({e['party'] for e in expense_data})

        # Ek filtreler
        if income_party:
            lp = income_party.lower()
            income_data = [i for i in income_data if lp in i['party'].lower()]
        if income_method != 'all':
            income_data = [i for i in income_data if (i['method'] or '').lower() == income_method.lower()]
        if income_status != 'all':
            ls = income_status.lower()
            income_data = [i for i in income_data if i['status'].lower() == ls]

        if expense_party:
            le = expense_party.lower()
            expense_data = [e for e in expense_data if le in (e['party'] or '').lower()]
        if expense_method != 'all':
            expense_data = [e for e in expense_data if (e['method'] or '').lower() == expense_method.lower()]
        if expense_status != 'all':
            ls = expense_status.lower()
            expense_data = [e for e in expense_data if e['status'].lower() == ls]

    except Exception:
        app.logger.exception('Failed to list transactions of project %s',
                             project_id)
        flash("İşlem listesi alınırken bir hata oluştu. Liste eksik olabilir.",
              "danger")
        income_data, expense_data = [], []
        project_name = "Bilinmiyor"
    finally:
        cur.close()
        conn.close()

    net_cash_flow = total_realized_income - total_realized_expense

    return render_template(
        'project_transactions.html',
        project_id=project_id,
        project_name=project_name,
        view_type=view_type,
        income_data=income_data,
        expense_data=expense_data,
        user_name=session.get('user_name'),
        total_realized_income=total_realized_income,
        total_realized_expense=total_realized_expense,
        net_cash_flow=net_cash_flow,
        start_date=start_date_str,
        end_date=end_date_str,
        income_party=income_party,
        income_method=income_method,
        income_status=income_status,
        expense_party=expense_party,
        expense_method=expense_method,
        expense_status=expense_status,
        income_parties=income_parties,
        expense_parties=expense_parties
    )


@app.route('/project/<int:project_id>/overview')
@login_required
def project_overview(project_id):
    conn = get_connection()
    cur = conn.cursor()
    
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    sort_by = request.args.get('sort_by', 'date')
    order = request.args.get('order', 'asc')
    income_party = request.args.get('income_party', '').strip()
    income_method = request.args.get('income_method', 'all')
    income_status = request.args.get('income_status', 'all')
    expense_party = request.args.get('expense_party', '').strip()
    expense_method = request.args.get('expense_method', 'all')
    expense_status = request.args.get('expense_status', 'all')

    income_items = []
    expense_items = []
    today = date.today()
    project_name = "Bilinmiyor"
    project_type = "normal"

    total_paid_income = Decimal(0)
    total_unpaid_income = Decimal(0)
    total_paid_expense = Decimal(0)
    total_unpaid_expense = Decimal(0)
    income_parties = []
    expense_parties = []

    try:
        # Safety net for out of range dates. See repair_out_of_range_dates.
        repair_out_of_range_dates(conn, cur, [
            ('payments', 'payment_date'),
            ('supplier_payments', 'payment_date'),
            ('outgoing_checks', 'due_date'),
            ('checks', 'due_date'),
            ('installment_schedule', 'due_date'),
        ], 'project_overview')

        cur.execute("SELECT name, project_type FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()
        project_name, project_type = project_info

        if project_type == 'normal':
            cur.execute("""
                SELECT COALESCE(SUM(p.amount), 0) 
                FROM payments p 
                JOIN flats f ON p.flat_id = f.id 
                LEFT JOIN checks c ON p.check_id = c.id 
                WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
            """, (project_id,))
            total_paid_income = cur.fetchone()[0]
            
            cur.execute("SELECT COALESCE(SUM(s.amount), 0) FROM installment_schedule s JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s", (project_id,))
            total_planned_income = cur.fetchone()[0]
            total_unpaid_income = total_planned_income - total_paid_income
        
        cur.execute("""
            SELECT COALESCE(SUM(sp.amount), 0) 
            FROM supplier_payments sp 
            JOIN expenses e ON sp.expense_id = e.id 
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id 
            WHERE e.project_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
        """, (project_id,))
        paid_large_expenses = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        paid_petty_cash = cur.fetchone()[0]
        total_paid_expense = paid_large_expenses + paid_petty_cash
        cur.execute("SELECT COALESCE(SUM(es.amount), 0) FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id WHERE e.project_id = %s", (project_id,))
        total_planned_large_expense = cur.fetchone()[0]
        total_unpaid_expense = (total_planned_large_expense + paid_petty_cash) - total_paid_expense

        if project_type == 'normal':
            income_query = """
                WITH flat_payment_summary AS (
                    SELECT p.flat_id,
                           COALESCE(SUM(p.amount) FILTER (WHERE p.payment_method = 'nakit'), 0) as total_cash,
                           COALESCE(SUM(p.amount) FILTER (WHERE p.payment_method = 'çek' AND c.status = 'tahsil_edildi'), 0) as total_cleared_check,
                           COALESCE(SUM(p.amount) FILTER (WHERE p.payment_method = 'çek' AND c.status = 'portfoyde'), 0) as total_portfolio_check
                    FROM payments p LEFT JOIN checks c ON p.check_id = c.id JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s GROUP BY p.flat_id
                ), cumulative_installments AS (
                    SELECT id, flat_id, amount, SUM(amount) OVER (PARTITION BY flat_id ORDER BY due_date, id) as cumulative_amount FROM installment_schedule
                )
                SELECT s.due_date, ci.amount, c.first_name, c.last_name, f.block_name, f.floor, f.flat_no,
                       COALESCE(fps.total_cash, 0), COALESCE(fps.total_cleared_check, 0),
                       COALESCE(fps.total_portfolio_check, 0), ci.cumulative_amount
                FROM installment_schedule s
                JOIN flats f ON s.flat_id = f.id JOIN customers c ON f.owner_id = c.id
                LEFT JOIN flat_payment_summary fps ON s.flat_id = fps.flat_id JOIN cumulative_installments ci ON s.id = ci.id
                WHERE f.project_id = %s
            """
            cur.execute(income_query, (project_id, project_id))
            for row in cur.fetchall():
                due_date, amount, first, last, block, floor, flat_no, total_cash, total_cleared_check, total_portfolio_check, cumulative_amount = row
                # An installment is closed only by money that really arrived:
                # cash, and checks marked 'tahsil_edildi'. A check still in the
                # portfolio closes nothing; it is shown but not counted. The
                # totals at the top of the page already work this way.
                total_cleared_payments = total_cash + total_cleared_check
                total_with_portfolio = total_cleared_payments + total_portfolio_check
                paid_so_far = max(0, total_cleared_payments - (cumulative_amount - amount))
                paid_this_installment = min(amount, paid_so_far)
                if paid_this_installment >= amount:
                    if cumulative_amount <= total_cash: status, status_class, payment_method = "Ödendi", "bg-success", "nakit"
                    else: status, status_class, payment_method = "Ödendi", "bg-success", "çek"
                elif total_with_portfolio >= cumulative_amount:
                    # A check would cover this installment, but it has not been
                    # cashed yet, so the debt is still open.
                    status, status_class, payment_method = "Çek Portföyde", "bg-warning text-dark", "çek"
                elif paid_this_installment > 0: 
                    status, status_class = "Kısmen Ödendi", "bg-info text-dark"
                    payment_method = None
                else: 
                    status, status_class = ("Gecikmiş", "bg-danger") if due_date < today else ("Bekleniyor", "bg-secondary")
                    payment_method = None
                income_items.append({
                    'date': due_date,
                    'description': "Daire Satış Taksiti",
                    'party': f"{first} {last}",
                    'details': f"Blok: {block or 'N/A'}, Kat: {floor}, No: {flat_no}",
                    'amount': amount,
                    'status': status,
                    'status_class': status_class,
                    'payment_method': payment_method
                })
        
        expense_query = """
            WITH expense_payment_summary AS (
                SELECT sp.expense_id,
                       COALESCE(SUM(sp.amount) FILTER (WHERE sp.payment_method = 'nakit'), 0) as total_cash,
                       COALESCE(SUM(sp.amount) FILTER (WHERE sp.payment_method = 'çek' AND oc.status = 'odendi'), 0) as total_cleared_check,
                       COALESCE(SUM(sp.amount) FILTER (WHERE sp.payment_method = 'çek' AND oc.status = 'verildi'), 0) as total_portfolio_check
                FROM supplier_payments sp
                LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
                JOIN expenses e ON sp.expense_id = e.id WHERE e.project_id = %s
                GROUP BY sp.expense_id
            ),
            cumulative_expense_installments AS (
                SELECT id, expense_id, amount,
                       SUM(amount) OVER (PARTITION BY expense_id ORDER BY due_date, id) as cumulative_amount
                FROM expense_schedule
            )
            SELECT s.due_date, cei.amount, e.title, sup.name,
                   COALESCE(eps.total_cash, 0), COALESCE(eps.total_cleared_check, 0),
                   COALESCE(eps.total_portfolio_check, 0), cei.cumulative_amount
            FROM expense_schedule s
            JOIN expenses e ON s.expense_id = e.id
            LEFT JOIN suppliers sup ON e.supplier_id = sup.id
            LEFT JOIN expense_payment_summary eps ON s.expense_id = eps.expense_id
            JOIN cumulative_expense_installments cei ON s.id = cei.id
            WHERE e.project_id = %s
        """
        cur.execute(expense_query, (project_id, project_id))
        for row in cur.fetchall():
            due_date, amount, title, sup_name, total_cash, total_cleared_check, total_portfolio_check, cumulative_amount = row
            
            # Same rule as the income side: an expense installment is closed
            # only by money that really left, that is cash and outgoing checks
            # marked 'odendi'. A check that is only handed over ('verildi')
            # closes nothing; it is shown but not counted.
            total_cleared_payments = total_cash + total_cleared_check
            total_with_portfolio = total_cleared_payments + total_portfolio_check
            paid_so_far = max(0, total_cleared_payments - (cumulative_amount - amount))
            paid_this_installment = min(amount, paid_so_far)
            
            if paid_this_installment >= amount:
                if cumulative_amount <= total_cash: 
                    status, status_class, payment_method = "Ödendi", "bg-success", "nakit"
                else: 
                    status, status_class, payment_method = "Ödendi", "bg-success", "çek"
            elif total_with_portfolio >= cumulative_amount:
                # A handed over check would cover this, but it is not paid yet.
                status, status_class, payment_method = "Çek Verildi", "bg-warning text-dark", "çek"
            elif paid_this_installment > 0:
                status, status_class = "Kısmen Ödendi", "bg-info text-dark"
                payment_method = None
            else:
                status, status_class = ("Gecikmiş", "bg-danger") if due_date < today else ("Bekleniyor", "bg-secondary")
                payment_method = None
                
            expense_items.append({'date': due_date, 'description': title, 'party': sup_name or "Belirtilmemiş", 'details': 'Planlı Gider', 'amount': amount, 'status': status, 'status_class': status_class, 'payment_method': payment_method})

        cur.execute("SELECT expense_date, title, amount, description FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        for expense_date, title, amount, desc in cur.fetchall():
            expense_items.append({'date': expense_date, 'description': title, 'party': 'Kasa', 'details': desc or 'Küçük Gider', 'amount': amount, 'status': 'Ödendi', 'status_class': 'bg-success', 'payment_method': 'nakit'})

        if start_date_str:
            start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] >= start_date_obj]
            expense_items = [e for e in expense_items if e['date'] >= start_date_obj]
        if end_date_str:
            end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] <= end_date_obj]
            expense_items = [e for e in expense_items if e['date'] <= end_date_obj]

        income_parties = sorted({i['party'] for i in income_items})
        expense_parties = sorted({e['party'] for e in expense_items})

        if income_party:
            lp = income_party.lower()
            income_items = [i for i in income_items if lp in i['party'].lower()]
        if income_method != 'all':
            income_items = [i for i in income_items if (i['payment_method'] or '').lower() == income_method.lower()]
        if income_status != 'all':
            ls = income_status.lower()
            income_items = [i for i in income_items if i['status'].lower().startswith(ls)]

        if expense_party:
            le = expense_party.lower()
            expense_items = [e for e in expense_items if le in (e['party'] or '').lower()]
        if expense_method != 'all':
            expense_items = [e for e in expense_items if (e['payment_method'] or '').lower() == expense_method.lower()]
        if expense_status != 'all':
            ls = expense_status.lower()
            expense_items = [e for e in expense_items if e['status'].lower().startswith(ls)]

        # --- YENİ EKLENEN KISIM: SUNUCU TABANLI KUSURSUZ SIRALAMA MANTIĞI ---
        is_reverse = (order == 'desc')
        
        def sort_logic(item, sort_col):
            if sort_col == 'date':
                return item.get('date') or today
            elif sort_col == 'party':
                return (item.get('party') or '').lower()
            elif sort_col == 'amount':
                return item.get('amount', Decimal(0))
            elif sort_col == 'status':
                return (item.get('status') or '').lower()
            return item.get('date') or today

        # Müşterileri gruplama özelliğini ezip, tamamen genel kurallara göre bağımsız sıralar
        income_items.sort(key=lambda x: sort_logic(x, sort_by), reverse=is_reverse)
        expense_items.sort(key=lambda x: sort_logic(x, sort_by), reverse=is_reverse)

    except Exception:
        app.logger.exception('Failed to build overview of project %s', project_id)
        flash("Proje genel bakışı oluşturulurken bir hata oluştu. Sayfa eksik "
              "olabilir.", "danger")
    finally:
        cur.close()
        conn.close()
    
    return render_template('project_overview.html', project_id=project_id, project_name=project_name, 
    income_items=income_items, expense_items=expense_items, total_paid_income=total_paid_income, total_unpaid_income=total_unpaid_income, total_paid_expense=total_paid_expense, total_unpaid_expense=total_unpaid_expense, user_name=session.get('user_name'), project_type=project_type, start_date=start_date_str, end_date=end_date_str, sort_by=sort_by, order=order,
    income_party=income_party, income_method=income_method, income_status=income_status,
    expense_party=expense_party, expense_method=expense_method, expense_status=expense_status,
    income_parties=income_parties, expense_parties=expense_parties)

# get_flats_for_project fonksiyonu

@app.route('/api/project/<int:project_id>/flats')
@json_login_required({'error': 'Yetkisiz erişim'})
def get_flats_for_project(project_id):
    """
    Bir projeye ait, sahibi olan daireleri listeler.
    Daire metninde blok, kat, no ve sahip ismini içerir.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
    
        cur.execute("""
            SELECT 
                f.id, f.flat_no, f.floor, f.room_type, f.block_name,
                c.first_name, c.last_name
            FROM flats f
            JOIN customers c ON f.owner_id = c.id
            WHERE f.project_id = %s AND f.owner_id IS NOT NULL
            ORDER BY f.block_name, f.floor, f.flat_no
        """, (project_id,))
        flats_raw = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    flats = [{
        'id': f[0], 
        'text': f"Blok: {f[4] or 'N/A'}, Kat: {f[2]}, No: {f[1]}  —  ({f[5]} {f[6]})"
    } for f in flats_raw]
    
    return jsonify(flats)
