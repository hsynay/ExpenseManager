"""The expense side: expenses, their payment plans, supplier payments and
petty cash."""
from flask import render_template, request, redirect, url_for, session, flash
from db import get_connection
from datetime import date, datetime
from itertools import groupby, zip_longest
import json
from decimal import Decimal
from core import app
from helpers import (
    ValidationError, parse_form_date, format_thousands, log_audit,
    claim_submission_token, PAGE_SIZE, get_page_number, page_window,
    build_pager, split_page, safe_next, login_required,
    repair_out_of_range_dates,
)
from reconcile import reconcile_supplier_payments


@app.route('/expenses', methods=['GET'])
@login_required
def list_expenses():
    conn = get_connection()
    cur = conn.cursor()

    project_id_str = request.args.get('project_id')
    expense_type = request.args.get('expense_type', 'all')
    title_filter = (request.args.get('title') or "").strip()
    supplier_id_str = request.args.get('supplier_id')
    current_query = request.query_string.decode() if request.query_string else ""
    
    # --- SIRALAMA PARAMETRELERİ ---
    pc_sort = request.args.get('pc_sort', 'date')
    pc_order = request.args.get('pc_order', 'desc')
    if pc_order not in ['asc', 'desc']:
        pc_order = 'desc'

    # Proje listesini her zaman çekiyoruz
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    all_projects = cur.fetchall()

    if not project_id_str or project_id_str == 'all':
        cur.close()
        conn.close()
        return render_template('expenses.html',
                               detailed_view=False,
                               all_projects=all_projects,
                               selected_project_id=None,
                               user_name=session.get('user_name'))

    # Validate the project id before any query. If it is broken we cannot
    # render the detailed view, so we go back to the project picker.
    try:
        project_id = int(project_id_str)
    except (TypeError, ValueError):
        cur.close()
        conn.close()
        flash("Geçersiz proje seçimi.", "danger")
        return render_template('expenses.html',
                               detailed_view=False,
                               all_projects=all_projects,
                               selected_project_id=None,
                               user_name=session.get('user_name'))

    expenses_data, petty_cash_items = [], []
    project_name = ""
    total_project_expense, total_paid_project, total_remaining_due = Decimal(0), Decimal(0), Decimal(0)
    total_petty_cash_expense = Decimal(0)
    supplier_list = []
    large_titles, petty_titles = [], []

    # The two lists on this page turn independently.
    page = get_page_number()
    pc_page = get_page_number('pc_page')
    expenses_pager = build_pager(page, has_next=False)
    petty_pager = build_pager(pc_page, has_next=False, arg_name='pc_page')

    try:
        # Safety net for out of range dates. See repair_out_of_range_dates.
        repair_out_of_range_dates(conn, cur, [
            ('petty_cash_expenses', 'expense_date'),
            ('expense_schedule', 'due_date'),
            ('expenses', 'expense_date'),
        ], '/expenses')

        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name = cur.fetchone()[0]

        cur.execute("""
            SELECT sp.expense_id, sp.id, sp.payment_date, sp.description, sp.amount, sp.payment_method,
                   sp.check_id, oc.status, oc.check_number, oc.due_date, oc.bank_name
            FROM supplier_payments sp
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
            WHERE sp.expense_id IN (SELECT id FROM expenses WHERE project_id = %s)
            ORDER BY sp.expense_id, sp.payment_date DESC, sp.id DESC
        """, (project_id,))
        payments_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

        cur.execute("""
            SELECT expense_id, due_date, amount, is_paid, paid_amount, id as installment_id
            FROM expense_schedule
            WHERE expense_id IN (SELECT id FROM expenses WHERE project_id = %s)
            ORDER BY expense_id, due_date ASC
        """, (project_id,))
        schedules_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

        # Filtre listeleri
        cur.execute("""
            SELECT DISTINCT s.id, s.name
            FROM suppliers s
            JOIN expenses e ON e.supplier_id = s.id
            WHERE e.project_id = %s
            ORDER BY s.name
        """, (project_id,))
        supplier_list = cur.fetchall()

        cur.execute("SELECT DISTINCT title FROM expenses WHERE project_id = %s ORDER BY title", (project_id,))
        large_titles = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT DISTINCT title FROM petty_cash_expenses WHERE project_id = %s ORDER BY title", (project_id,))
        petty_titles = [row[0] for row in cur.fetchall()]

        # Büyük giderler
        expenses_raw = []
        if expense_type in ('all', 'large'):
            expenses_sql = """
                SELECT e.id, e.title, e.amount, s.name as supplier_name, e.supplier_id
                FROM expenses e
                LEFT JOIN suppliers s ON e.supplier_id = s.id
                WHERE e.project_id = %s
            """
            expenses_params = [project_id]
            if supplier_id_str:
                expenses_sql += " AND e.supplier_id = %s"
                expenses_params.append(int(supplier_id_str))
            if title_filter:
                expenses_sql += " AND e.title ILIKE %s"
                expenses_params.append(f"%{title_filter}%")
            expenses_sql += " ORDER BY e.id DESC"
            large_limit, large_offset = page_window(page)
            expenses_sql += " LIMIT %s OFFSET %s"
            expenses_params.extend([large_limit, large_offset])
            cur.execute(expenses_sql, tuple(expenses_params))
            expenses_raw, expenses_pager = split_page(cur.fetchall(), page)

        # Küçük giderler
        if expense_type in ('all', 'petty'):
            petty_sql = """
                SELECT id, title, amount, expense_date, description
                FROM petty_cash_expenses
                WHERE project_id = %s
            """
            petty_params = [project_id]
            if title_filter:
                petty_sql += " AND title ILIKE %s"
                petty_params.append(f"%{title_filter}%")
            
            cur.execute(petty_sql, tuple(petty_params))
            raw_petty = cur.fetchall()

            # Python ile kesin sıralama
            is_reverse = (pc_order == 'desc')
            if pc_sort == 'amount':
                petty_sorted = sorted(raw_petty, key=lambda x: (x[2], x[3], x[0]), reverse=is_reverse)
            else:
                petty_sorted = sorted(raw_petty, key=lambda x: (x[3], x[0]), reverse=is_reverse)

            # The total belongs to the whole list, so read it before cutting
            # the page out. Sorting happens here rather than in SQL, so the
            # page is cut here too.
            total_petty_cash_expense = sum(item[2] for item in petty_sorted)
            start = (pc_page - 1) * PAGE_SIZE
            petty_cash_items = petty_sorted[start:start + PAGE_SIZE]
            petty_pager = build_pager(
                pc_page, has_next=len(petty_sorted) > start + PAGE_SIZE,
                arg_name='pc_page')
        else:
            petty_cash_items = []
            total_petty_cash_expense = Decimal(0)

        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
        total_planned_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        total_petty_cash = cur.fetchone()[0]
        total_project_expense = total_planned_expense + total_petty_cash
        cur.execute("""
            SELECT COALESCE(SUM(sp.amount), 0) 
            FROM supplier_payments sp 
            JOIN expenses e ON sp.expense_id = e.id 
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id 
            WHERE e.project_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
        """, (project_id,))
        total_paid_scheduled = cur.fetchone()[0] or Decimal(0)
        total_paid_project = (total_paid_scheduled or Decimal(0)) + (total_petty_cash or Decimal(0))
        total_remaining_due = total_project_expense - total_paid_project

        today = date.today()
        for expense_id_loop, title, total_amount, supplier_name, _supplier_id in expenses_raw:
            schedule = schedules_by_expense.get(expense_id_loop, [])
            total_paid_for_this_expense = sum(item[4] for item in schedule if item[4])
            expense_dict = {
                'expense_id': expense_id_loop, 'title': title, 'supplier_name': supplier_name or "-",
                'total_amount': total_amount, 'total_paid': total_paid_for_this_expense,
                'remaining_due': total_amount - total_paid_for_this_expense, 
                'installments': [],
                'payments': payments_by_expense.get(expense_id_loop, [])
            }
            for _, due_date, inst_amount, is_paid, paid_amount, inst_id in schedule:
                paid_amount = paid_amount or Decimal(0)
                status, css_class = ("Ödendi", "table-success") if is_paid else ("Kısmen Ödendi", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
                expense_dict['installments'].append({
                    'id': inst_id, 'due_date': due_date, 'total_amount': inst_amount,
                    'remaining_installment_due': inst_amount - paid_amount,
                    'status': status, 'css_class': css_class, 'is_paid': is_paid
                })
            
            expense_dict['installments'].sort(key=lambda x: x['due_date'])
            expenses_data.append(expense_dict)
            
    except Exception:
        app.logger.exception('Failed to list expenses (project_id=%s)',
                             project_id_str)
        flash("Giderler listelenirken bir hata oluştu. Liste eksik olabilir.",
              "danger")
    finally:
        cur.close()
        conn.close()

    return render_template('expenses.html',
                           detailed_view=True, project_id=project_id, project_name=project_name,
                           expenses_data=expenses_data, 
                           petty_cash_items=petty_cash_items,
                           total_petty_cash_expense=total_petty_cash_expense,
                           total_project_expense=total_project_expense,
                           total_paid_project=total_paid_project,
                           total_remaining_due=total_remaining_due,
                           all_projects=all_projects, selected_project_id=str(project_id),
                           expense_type=expense_type,
                           title_filter=title_filter,
                           supplier_id=supplier_id_str,
                           supplier_list=supplier_list,
                           large_titles=large_titles,
                           petty_titles=petty_titles,
                           current_query=current_query,
                           pc_sort=pc_sort,            
                           pc_order=pc_order,
                           expenses_pager=expenses_pager,
                           petty_pager=petty_pager,
                           user_name=session.get('user_name'))


# Bu fonksiyon artık ana giriş noktasıdır.
@app.route('/expenses/select')
def select_project_for_expenses():
    return redirect(url_for('list_expenses'))

# app.py içine eklenecek/değiştirilecek fonksiyonlar


@app.route('/supplier_payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_payment(payment_id):
    conn = get_connection()
    cur = conn.cursor()
    next_url = safe_next(request.form.get('next') or request.args.get('next'))
    
    # --- FORM GÖNDERİLDİĞİNDE (POST İSTEĞİ) ---
    if request.method == 'POST':
        try:
            amount_str = request.form.get('amount').replace('.', '').replace(',', '.')
            amount = Decimal(amount_str)
            payment_date = parse_form_date(request.form.get('payment_date'),
                                           "Ödeme tarihi")
            description = request.form.get('description')
            
            cur.execute("SELECT expense_id, check_id FROM supplier_payments WHERE id = %s", (payment_id,))
            result = cur.fetchone()
            if not result:
                raise ValidationError("Güncellenecek ödeme kaydı bulunamadı.")
            expense_id, check_id = result

            cur.execute("UPDATE supplier_payments SET amount=%s, payment_date=%s, description=%s WHERE id=%s",
                        (amount, payment_date, description, payment_id))

            if check_id:
                check_due_date = parse_form_date(
                    request.form.get('check_due_date'), "Çek vade tarihi")
                cur.execute("UPDATE outgoing_checks SET amount = %s, issue_date = %s, due_date = %s WHERE id = %s",
                            (amount, payment_date, check_due_date, check_id))

            reconcile_supplier_payments(cur, expense_id)
            
            conn.commit()
            flash('Gider ödemesi başarıyla güncellendi.', 'success')
            
            cur.execute("SELECT project_id FROM expenses WHERE id = %s", (expense_id,))
            project_id = cur.fetchone()[0]
            return redirect(next_url or url_for('list_expenses', project_id=project_id))

        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update supplier payment %s', payment_id)
            flash('Tedarikçi ödemesi güncellenirken bir hata oluştu. '
                  'Değişiklikler kaydedilmedi.', 'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('edit_supplier_payment', payment_id=payment_id,
                                next=next_url or None))

    # --- SAYFA İLK AÇILDIĞINDA (GET İSTEĞİ) ---
    # DÜZELTME BURADA: Veritabanından gelen 'tuple' verisini bir 'dictionary' (sözlük) haline getiriyoruz.
    try:
        cur.execute("""
            SELECT 
                sp.amount, sp.payment_date, sp.description, e.title, p.name, e.project_id,
                sp.check_id, oc.due_date as check_due_date
            FROM supplier_payments sp
            JOIN expenses e ON sp.expense_id = e.id
            JOIN projects p ON e.project_id = p.id
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
            WHERE sp.id = %s
        """, (payment_id,))
        payment_raw = cur.fetchone() # Bu satır veriyi bir tuple (liste) olarak alır

        if not payment_raw:
            flash('Düzenlenecek ödeme bulunamadı.', 'danger')
            return redirect(url_for('dashboard'))

        # Aldığımız tuple'ı, HTML şablonunun anlayacağı bir sözlüğe dönüştürüyoruz
        payment = {
            'amount': payment_raw[0],
            'payment_date': payment_raw[1],
            'description': payment_raw[2],
            'expense_title': payment_raw[3],
            'project_name': payment_raw[4],
            'project_id': payment_raw[5],
            'is_check': payment_raw[6] is not None,
            'check_due_date': payment_raw[7]
        }

    except Exception:
        app.logger.exception('Failed to load supplier payment %s', payment_id)
        flash('Tedarikçi ödemesi bilgileri alınırken bir hata oluştu.', 'danger')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()
        conn.close()

    return render_template('edit_supplier_payment.html', payment=payment, payment_id=payment_id,
                           next_url=next_url or '', user_name=session.get('user_name'))

@app.route('/supplier_payment/<int:payment_id>/delete', methods=['POST'])
@login_required
def delete_supplier_payment(payment_id):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT expense_id FROM supplier_payments WHERE id = %s", (payment_id,))
        expense_id = cur.fetchone()[0]
        cur.execute("DELETE FROM supplier_payments WHERE id = %s", (payment_id,))
        reconcile_supplier_payments(cur, expense_id)
        log_audit(cur, session.get('user_id'), 'supplier_payment_delete', 'supplier_payment', payment_id,
                  {'expense_id': expense_id})
        conn.commit()
        flash('Gider ödemesi silindi ve ilgili taksitler güncellendi.', 'success')
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to delete supplier payment %s', payment_id)
        flash('Tedarikçi ödemesi silinirken bir hata oluştu. Ödeme silinmedi.',
              'danger')
    finally:
        cur.close()
        conn.close()
    project_id = request.form.get('project_id')
    next_url = safe_next(request.form.get('next'))
    return redirect(next_url or url_for('list_expenses', project_id=project_id))


# new_supplier_payment fonksiyonu

@app.route('/supplier_payment/new', methods=['GET', 'POST'])
@login_required
def new_supplier_payment():
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot record the payment twice.
            # project_id is read from the form because it is not parsed yet.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'new_supplier_payment'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(url_for('list_expenses', project_id=request.form.get('project_id')))

            supplier_id = int(request.form.get('supplier_id'))
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date')
            project_id = int(request.form.get('project_id')) # Ödemeyi bir projeyle ilişkilendirmek için
            description = request.form.get('description', 'Tedarikçi Ödemesi')

            if not all([supplier_id, payment_amount, payment_date_str, project_id]):
                raise ValidationError("Tüm zorunlu alanlar doldurulmalıdır.")

            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()

            # Ödemeyi taksitlere dağıtma
            amount_to_distribute = payment_amount
            cur.execute("""
                SELECT es.id, es.amount, es.paid_amount, es.expense_id
                FROM expense_schedule es
                JOIN expenses e ON es.expense_id = e.id
                WHERE e.supplier_id = %s AND e.project_id = %s AND es.is_paid = FALSE
                ORDER BY es.due_date ASC
            """, (supplier_id, project_id))
            
            unpaid_installments = cur.fetchall()

            if not unpaid_installments:
                flash("Bu tedarikçinin seçilen projeye ait ödenmemiş bir borcu bulunamadı.", "warning")
                return redirect(url_for('new_supplier_payment'))

            paid_expense_ids = set()
            for inst_id, total_amount, paid_amount, expense_id in unpaid_installments:
                if amount_to_distribute <= 0: break
                
                paid_expense_ids.add(expense_id)
                remaining_due = total_amount - paid_amount
                
                if amount_to_distribute >= remaining_due:
                    cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, inst_id))
                    amount_to_distribute -= remaining_due
                else:
                    new_paid_amount = paid_amount + amount_to_distribute
                    cur.execute("UPDATE expense_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, inst_id))
                    amount_to_distribute = 0
            
            first_expense_id = list(paid_expense_ids)[0] if paid_expense_ids else None
            
            # Fiili ödemeyi supplier_payments tablosuna kaydet
            cur.execute("""
                INSERT INTO supplier_payments (expense_id, supplier_id, amount, payment_date, payment_method, description)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (first_expense_id, supplier_id, payment_amount, payment_date, 'nakit', description))
            
            log_audit(cur, session.get('user_id'), 'supplier_payment_create', 'supplier_payment', None,
                      {'supplier_id': supplier_id, 'project_id': project_id, 'amount': float(payment_amount), 'expense_ids': list(paid_expense_ids)})

            conn.commit()
            flash(f'{format_thousands(payment_amount)} ₺ tutarındaki tedarikçi ödemesi kaydedildi ve borçlara yansıtıldı.', 'success')
            return redirect(url_for('list_expenses', project_id=project_id))

        except ValidationError as e:
            if conn: conn.rollback()
            flash(str(e), 'danger')
            return redirect(url_for('new_supplier_payment'))
        except Exception:
            if conn: conn.rollback()
            app.logger.exception('Failed to save supplier payment')
            flash('Tedarikçi ödemesi kaydedilirken bir hata oluştu. Ödeme '
                  'kaydedilmedi.', 'danger')
            return redirect(url_for('new_supplier_payment'))
        finally:
            if conn:
                cur.close()
                conn.close()

    cur.execute("SELECT id, name FROM suppliers ORDER BY name")
    suppliers = cur.fetchall()
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('new_supplier_payment.html', 
                           suppliers=suppliers,
                           projects=projects,
                           user_name=session.get('user_name'))


# @app.route('/project/<int:project_id>/expense/new', methods=['GET', 'POST'])
# def add_expense(project_id):
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     conn = get_connection()
#     cur = conn.cursor()

#     if request.method == 'POST':
#         try:
#             title = request.form['title']
#             description = request.form.get('description', '')
            
#             # Tedarikçi işlemleri
#             supplier_option = request.form.get('supplier_option')
#             supplier_id = None
#             if supplier_option == 'new':
#                 new_supplier_name = request.form.get('new_supplier_name')
#                 if not new_supplier_name: raise ValueError("Yeni tedarikçi adı zorunludur.")
#                 cur.execute(
#                     "INSERT INTO suppliers (name, project_id, category) VALUES (%s, %s, %s) RETURNING id",
#                     (new_supplier_name, project_id, request.form.get('new_supplier_category'))
#                 )
#                 supplier_id = cur.fetchone()[0]
#             else:
#                 supplier_id_val = request.form.get('supplier_id')
#                 if not supplier_id_val: raise ValueError("Lütfen bir tedarikçi seçin.")
#                 supplier_id = int(supplier_id_val)

#             # Taksit verilerini al
#             due_dates = request.form.getlist('installment_due_date[]')
#             amounts_str = request.form.getlist('installment_amount[]')
            
#             # Toplam tutarı taksitlerden hesapla
#             valid_installments = []
#             total_amount = Decimal(0)
            
#             for d_str, a_str in zip(due_dates, amounts_str):
#                 if d_str and a_str:
#                     amt = Decimal(a_str)
#                     total_amount += amt
#                     valid_installments.append((d_str, amt))
            
#             if not valid_installments:
#                 raise ValueError("En az bir taksit/ödeme girişi yapılmalıdır.")

#             # 1. Ana gider kaydını oluştur
#             cur.execute(
#                 "INSERT INTO expenses (project_id, title, amount, expense_date, description, supplier_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
#                 (project_id, title, total_amount, datetime.now().date(), description, supplier_id)
#             )
#             expense_id = cur.fetchone()[0]

#             # 2. Taksitleri (Ödeme Planını) kaydet
#             for d_str, amt in valid_installments:
#                 due_date = datetime.strptime(d_str, '%Y-%m-%d').date()
#                 cur.execute(
#                     "INSERT INTO expense_schedule (expense_id, due_date, amount) VALUES (%s, %s, %s)",
#                     (expense_id, due_date, amt)
#                 )
            
#             conn.commit()
#             flash('Yeni gider ve ödeme planı başarıyla tanımlandı.', 'success')
#             return redirect(url_for('list_expenses', project_id=project_id))

#         except Exception as e:
#             conn.rollback()
#             flash(f'Gider eklenirken bir hata oluştu: {e}', 'danger')
#         finally:
#             cur.close()
#             conn.close()
#         return redirect(url_for('add_expense', project_id=project_id))

#     # GET Metodu
#     cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
#     project = cur.fetchone()
#     cur.execute("SELECT id, name FROM suppliers WHERE project_id = %s ORDER BY name", (project_id,))
#     suppliers = cur.fetchall()
#     cur.close()
#     conn.close()

#     return render_template('new_expense.html', 
#                            project_name=project[0], 
#                            project_id=project_id,
#                            suppliers=suppliers,
#                            user_name=session.get('user_name'))

@app.route('/project/<int:project_id>/expense/new', methods=['GET', 'POST'])
@login_required
def add_expense(project_id):
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot create the expense, its
            # schedule and a new supplier twice.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'add_expense'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(url_for('list_expenses', project_id=project_id))

            title = request.form['title']
            description = request.form.get('description', '')
            
            # Tedarikçi işlemleri
            supplier_option = request.form.get('supplier_option')
            supplier_id = None
            if supplier_option == 'new':
                new_supplier_name = request.form.get('new_supplier_name')
                if not new_supplier_name: raise ValidationError("Yeni tedarikçi adı zorunludur.")
                cur.execute(
                    "INSERT INTO suppliers (name, project_id, category) VALUES (%s, %s, %s) RETURNING id",
                    (new_supplier_name, project_id, request.form.get('new_supplier_category'))
                )
                supplier_id = cur.fetchone()[0]
            else:
                supplier_id_val = request.form.get('supplier_id')
                if not supplier_id_val: raise ValidationError("Lütfen bir tedarikçi seçin.")
                supplier_id = int(supplier_id_val)

            # --- DÜZELTİLEN KISIM: JSON İLE TAKSİTLERİ ALMA ---
            plan_json = request.form.get('plan_json')
            valid_installments = []
            total_amount = Decimal(0)

            if plan_json:
                rows_raw = json.loads(plan_json)
                for row in rows_raw:
                    d_str = (row.get('due_date') or "").strip()
                    a_str = (row.get('amount') or "").strip()
                    if d_str and a_str:
                        due_date = datetime.strptime(d_str, '%Y-%m-%d').date()
                        # JavaScript zaten temizlemişti, doğrudan Decimal'e çevirebiliriz
                        amt = Decimal(a_str) 
                        total_amount += amt
                        valid_installments.append((due_date, amt))
            else:
                # JSON gelmezse (Fallback / Güvenlik için eski yöntem)
                due_dates = request.form.getlist('installment_due_date[]')
                amounts_str = request.form.getlist('installment_amount[]')
                for d_str, a_str in zip_longest(due_dates, amounts_str, fillvalue=""):
                    if d_str and a_str:
                        due_date = datetime.strptime(d_str, '%Y-%m-%d').date()
                        amt = Decimal(a_str.replace(' ', '').replace('.', '').replace(',', '.'))
                        total_amount += amt
                        valid_installments.append((due_date, amt))

            if not valid_installments:
                raise ValidationError("En az bir taksit/ödeme girişi yapılmalıdır.")

            # *** KRİTİK: Taksitleri veri tabanına yazmadan önce KESİNLİKLE kronolojik sıraya sok ***
            valid_installments.sort(key=lambda x: x[0])

            # 1. Ana gider kaydını oluştur
            cur.execute(
                "INSERT INTO expenses (project_id, title, amount, expense_date, description, supplier_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (project_id, title, total_amount, datetime.now().date(), description, supplier_id)
            )
            expense_id = cur.fetchone()[0]

            # 2. Taksitleri (Ödeme Planını) sırasıyla kaydet
            for due_date, amt in valid_installments:
                cur.execute(
                    "INSERT INTO expense_schedule (expense_id, due_date, amount, is_paid, paid_amount) VALUES (%s, %s, %s, FALSE, 0)",
                    (expense_id, due_date, amt)
                )
            
            conn.commit()
            flash('Yeni gider ve ödeme planı başarıyla tanımlandı.', 'success')
            return redirect(url_for('list_expenses', project_id=project_id))

        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to add expense to project %s', project_id)
            flash('Gider eklenirken bir hata oluştu. Gider kaydedilmedi.',
                  'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('add_expense', project_id=project_id))

    # GET Metodu
    cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    cur.execute("SELECT id, name FROM suppliers WHERE project_id = %s ORDER BY name", (project_id,))
    suppliers = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('new_expense.html', 
                           project_name=project[0], 
                           project_id=project_id,
                           suppliers=suppliers,
                           user_name=session.get('user_name'))

# pay_expense_installment fonksiyonu

@app.route('/expense_installment/<int:installment_id>/pay', methods=['GET', 'POST'])
@login_required
def pay_expense_installment(installment_id):
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = safe_next(request.form.get('next') or request.args.get('next'))

            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot record the payment and its
            # outgoing check twice.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'pay_expense_installment'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(next_url or url_for('pay_expense_installment', installment_id=installment_id))

            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date')
            payment_method = request.form.get('payment_method', 'nakit')
            description = request.form.get('description', '')
            
            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()
            
            cur.execute("SELECT expense_id, amount, paid_amount FROM expense_schedule WHERE id = %s", (installment_id,))
            inst = cur.fetchone()
            if not inst: raise ValidationError("Ödeme yapılacak taksit bulunamadı.")
            expense_id, total_due, already_paid = inst

            remaining_due = total_due - (already_paid or 0)
            if payment_amount > remaining_due:
                flash(f"Ödeme tutarı, taksitin kalan borcundan ({remaining_due} ₺) fazla olamaz.", "warning")
                return redirect(url_for('pay_expense_installment', installment_id=installment_id))

            cur.execute("SELECT project_id, supplier_id FROM expenses WHERE id = %s", (expense_id,))
            expense_info_data = cur.fetchone()
            project_id, supplier_id = expense_info_data
            
            if payment_method == 'çek':
                due_date_str = request.form.get('check_due_date')
                if not due_date_str: raise ValidationError("Çek için vade tarihi zorunludur.")
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                
                # 1. Çeki kaydet
                cur.execute(
                    "INSERT INTO outgoing_checks (supplier_id, bank_name, check_number, amount, issue_date, due_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (supplier_id, request.form.get('check_bank_name'), request.form.get('check_number'), payment_amount, payment_date, due_date)
                )
                outgoing_check_id = cur.fetchone()[0]
                
                # 2. Çek ödemesini supplier_payments tablosuna bağla
                cur.execute(
                    "INSERT INTO supplier_payments (expense_id, supplier_id, amount, payment_date, payment_method, description, check_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (expense_id, supplier_id, payment_amount, payment_date, payment_method, description, outgoing_check_id)
                )
                flash(f'Çek başarıyla kaydedildi. Taksit, çek ödendiğinde güncellenecektir.', 'info')
            
            else: 
                # Sadece nakit ödemeyi kaydet
                cur.execute(
                    "INSERT INTO supplier_payments (expense_id, supplier_id, amount, payment_date, payment_method, description) VALUES (%s, %s, %s, %s, %s, %s)",
                    (expense_id, supplier_id, payment_amount, payment_date, 'nakit', description)
                )
                reconcile_supplier_payments(cur, expense_id)
                flash("Nakit ödeme başarıyla kaydedildi.", "success")

            # MÜKERRER KAYIT YAPAN ORTAK INSERT BURADAN KALDIRILDI!
            
            conn.commit()
            return redirect(next_url or url_for('list_expenses', project_id=project_id))

        except ValidationError as e:
            if conn: conn.rollback()
            flash(str(e), "danger")
        except Exception:
            if conn: conn.rollback()
            app.logger.exception('Failed to pay expense installment %s',
                                 installment_id)
            flash("Taksit ödemesi kaydedilirken bir hata oluştu. Ödeme "
                  "kaydedilmedi.", "danger")
        finally:
            cur.close()
            conn.close()
        return redirect(next_url or url_for('pay_expense_installment', installment_id=installment_id))

    # GET isteği için: Taksit bilgilerini al
    cur.execute("""
        SELECT es.id, es.due_date, es.amount, es.paid_amount, e.title, p.name, p.id as project_id
        FROM expense_schedule es
        JOIN expenses e ON es.expense_id = e.id
        JOIN projects p ON e.project_id = p.id
        WHERE es.id = %s
    """, (installment_id,))
    installment = cur.fetchone()
    cur.close()
    conn.close()

    if not installment:
        flash("Taksit bulunamadı.", "danger")
        return redirect(url_for('dashboard'))

    return render_template('pay_expense_installment.html', 
                           installment=installment,
                           user_name=session.get('user_name'),
                           next_url=request.args.get('next', ''))


@app.route('/project/<int:project_id>/petty_cash/add', methods=['POST'])
@login_required
def add_petty_cash(project_id):
    """Bir projeye yeni bir küçük gider ekler."""
    # Read this before the try block. The last line of this function needs it
    # even when the amount below cannot be parsed, and reading it inside the
    # try would leave it undefined on that path.
    next_url = safe_next(request.form.get('next'))

    try:
        title = request.form.get('petty_cash_title')
        amount = Decimal(request.form.get('petty_cash_amount').replace('.', '').replace(',', '.'))
        expense_date = parse_form_date(request.form.get('petty_cash_date'),
                                       "Tarih")
        description = request.form.get('petty_cash_description')

        if not all([title, amount, expense_date]):
            raise ValidationError("Başlık, Tutar ve Tarih alanları zorunludur.")
        
        conn = get_connection()
        cur = conn.cursor()

        # Double submit guard, in the same transaction as the insert below.
        if not claim_submission_token(cur, request.form.get('submission_token'), 'add_petty_cash'):
            conn.rollback()
            flash('Bu işlem zaten kaydedilmişti.', 'info')
            return redirect(next_url or url_for('list_expenses', project_id=project_id))

        cur.execute(
            "INSERT INTO petty_cash_expenses (project_id, title, amount, expense_date, description) VALUES (%s, %s, %s, %s, %s)",
            (project_id, title, amount, expense_date, description)
        )
        conn.commit()
        flash("Küçük gider başarıyla eklendi.", "success")

    except ValidationError as e:
        flash(str(e), "danger")
    except Exception:
        app.logger.exception('Failed to add petty cash expense to project %s',
                             project_id)
        flash("Küçük gider eklenirken bir hata oluştu. Kayıt yapılmadı, "
              "lütfen bilgileri kontrol edip tekrar deneyin.", "danger")
    finally:
        if 'conn' in locals() and conn:
            cur.close()
            conn.close()

    return redirect(next_url or url_for('list_expenses', project_id=project_id))


@app.route('/petty_cash/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_petty_cash(item_id):
    project_id = request.form.get('project_id')
    next_url = safe_next(request.form.get('next'))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM petty_cash_expenses WHERE id = %s", (item_id,))
        conn.commit()
        flash('Küçük gider kaydı silindi.', 'success')
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to delete petty cash expense %s', item_id)
        flash('Küçük gider silinirken bir hata oluştu. Kayıt silinmedi.',
              'danger')
    finally:
        cur.close()
        conn.close()

    if project_id:
        return redirect(next_url or url_for('list_expenses', project_id=project_id))
    return redirect(next_url or url_for('dashboard'))


@app.route('/petty_cash/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_petty_cash(item_id):
    conn = get_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title')
        amount_raw = request.form.get('amount') or '0'
        amount = Decimal(amount_raw.replace('.', '').replace(',', '.'))
        description = request.form.get('description')
        project_id = request.args.get('project_id') or request.form.get('project_id')
        next_url = safe_next(request.form.get('next'))
        try:
            expense_date = parse_form_date(request.form.get('expense_date'),
                                           "Tarih")
            cur.execute("UPDATE petty_cash_expenses SET title=%s, amount=%s, expense_date=%s, description=%s WHERE id=%s",
                        (title, amount, expense_date, description, item_id))
            conn.commit()
            flash('Küçük gider güncellendi.', 'success')
        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update petty cash expense %s', item_id)
            flash('Küçük gider güncellenirken bir hata oluştu. Değişiklikler '
                  'kaydedilmedi.', 'danger')
        finally:
            cur.close()
            conn.close()
        if project_id:
            return redirect(next_url or url_for('list_expenses', project_id=project_id))
        return redirect(next_url or url_for('dashboard'))

    # GET
    cur.execute("SELECT id, title, amount, expense_date, description, project_id FROM petty_cash_expenses WHERE id = %s", (item_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        flash('Kayıt bulunamadı.', 'danger')
        return redirect(url_for('dashboard'))
    item = {
        'id': row[0], 'title': row[1], 'amount': row[2], 'expense_date': row[3].isoformat() if row[3] else '', 'description': row[4]
    }
    return render_template('edit_petty_cash.html', item=item, project_id=row[5], next_url=request.args.get('next', ''))

# 4. Gider (Tedarikçi) Planı Yönetme Rotası
@app.route('/expense/<int:expense_id>/manage_plan', methods=['GET', 'POST'])
@login_required
def manage_expense_plan(expense_id):
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = safe_next(request.form.get('next') or request.args.get('next'))
            plan_json = request.form.get('plan_json')
            rows_raw = []
            if plan_json:
                rows_raw = json.loads(plan_json)
            else:
                due_dates = request.form.getlist('due_date[]')
                amounts_str = request.form.getlist('amount[]')
                rows_raw = [{'due_date': d, 'amount': a} for d, a in zip_longest(due_dates, amounts_str, fillvalue="")]

            parsed_rows = []
            for row in rows_raw:
                d_str = (row.get('due_date') or "").strip()
                a_str = (row.get('amount') or "").strip()
                if not d_str or not a_str:
                    continue
                due_date = datetime.strptime(d_str, '%Y-%m-%d').date()
                cleaned = a_str.replace(' ', '').replace('.', '').replace(',', '.')
                amount = Decimal(cleaned)
                parsed_rows.append((due_date, amount))

            if not parsed_rows:
                raise ValidationError("En az bir taksit girilmelidir.")

            parsed_rows.sort(key=lambda x: x[0])

            cur.execute("DELETE FROM expense_schedule WHERE expense_id = %s", (expense_id,))
            for due_date, amount in parsed_rows:
                cur.execute("""
                    INSERT INTO expense_schedule (expense_id, due_date, amount, is_paid, paid_amount)
                    VALUES (%s, %s, %s, FALSE, 0)
                """, (expense_id, due_date, amount))
            
            reconcile_supplier_payments(cur, expense_id)

            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expense_schedule WHERE expense_id = %s", (expense_id,))
            total_amount = cur.fetchone()[0]
            cur.execute("UPDATE expenses SET amount = %s WHERE id = %s", (total_amount, expense_id))

            # Audit log
            log_audit(
                cur,
                session.get('user_id'),
                'plan_update',
                'expense_plan',
                expense_id,
                {'rows': len(parsed_rows), 'total_amount': float(total_amount)}
            )

            conn.commit()
            flash('Gider ödeme planı başarıyla güncellendi!', 'success')
            
            cur.execute("SELECT project_id FROM expenses WHERE id = %s", (expense_id,))
            return redirect(next_url or url_for('list_expenses', project_id=cur.fetchone()[0]))

        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update the plan of expense %s',
                                 expense_id)
            flash('Gider planı güncellenirken bir hata oluştu. Plan '
                  'değiştirilmedi.', 'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(next_url or url_for('manage_expense_plan', expense_id=expense_id))

    # GET kısmı
    # *** GÜNCELLEME: Sıralama 'due_date ASC, id ASC' yapıldı ***
    cur.execute("SELECT due_date, amount, is_paid, id, paid_amount FROM expense_schedule WHERE expense_id = %s ORDER BY due_date ASC, id ASC", (expense_id,))
    existing_installments = cur.fetchall()
    
    cur.execute("SELECT e.title, p.name, e.project_id FROM expenses e JOIN projects p ON e.project_id = p.id WHERE e.id = %s", (expense_id,))
    expense_info = cur.fetchone()
    
    cur.close()
    conn.close()
    return render_template('manage_expense_plan.html', expense_id=expense_id, expense_info=expense_info, existing_installments=existing_installments, next_url=request.args.get('next', ''))


@app.route('/expense/<int:expense_id>/delete', methods=['POST'])
@login_required
def delete_expense(expense_id):
    """Belirli bir gideri ve varsa ilişkili çekini veritabanından siler."""
    project_id = request.form.get('project_id')

    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Silmeden önce ilişkili GİDER çekinin ID'sini al
        cur.execute("SELECT outgoing_check_id FROM expenses WHERE id = %s", (expense_id,))
        result = cur.fetchone()
        outgoing_check_id = result[0] if result else None

        # 2. Gideri sil (Veritabanındaki ON DELETE CASCADE ayarı ilgili taksitleri vs. otomatik siler)
        cur.execute("DELETE FROM expenses WHERE id = %s", (expense_id,))

        # 3. Eğer ilişkili bir çek varsa, onu da `outgoing_checks` tablosundan sil
        if outgoing_check_id:
            cur.execute("DELETE FROM outgoing_checks WHERE id = %s", (outgoing_check_id,))

        log_audit(cur, session.get('user_id'), 'expense_delete', 'expense', expense_id,
                  {'outgoing_check_id': outgoing_check_id, 'project_id': project_id})

        conn.commit()
        flash('Gider ve varsa ilgili çeki başarıyla silindi.', 'success')
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to delete expense %s', expense_id)
        flash('Gider silinirken bir hata oluştu. Gider silinmedi, veriler '
              'korundu.', 'danger')
    finally:
        cur.close()
        conn.close()

    if project_id:
        return redirect(url_for('list_expenses', project_id=project_id))
    else:
        return redirect(url_for('dashboard'))
