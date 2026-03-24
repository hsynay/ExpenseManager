from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify 
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from flask import Flask, render_template, request, redirect, url_for, session, flash
from db import get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from parser import parse_whatsapp_message
import os
from datetime import datetime
from itertools import groupby, zip_longest
import json
from datetime import date
from decimal import Decimal 
from flask import jsonify

app = Flask(__name__)
app.secret_key = os.urandom(24)  


# Jinja filter: format numbers like Turkish style (e.g. 2600000 -> 2.600.000)
def format_thousands(value):
    """Format a number with dot as thousands separator and comma as decimal separator.

    - Accepts int, float, Decimal or numeric strings.
    - Returns an empty string for None.
    - Examples: 2600000 -> '2.600.000', 12345.67 -> '12.345,67'
    """
    if value is None:
        return ""
    try:
        # Use Decimal for stable representation
        d = Decimal(value)
    except Exception:
        try:
            d = Decimal(str(value))
        except Exception:
            return str(value)

    sign = '-' if d < 0 else ''
    d = abs(d)

    # integer and fractional parts
    int_part = int(d // 1)
    frac_part = d - int_part

    # format integer part with commas then replace with dots
    int_str = f"{int_part:,}".replace(",", ".")

    if frac_part == 0:
        return sign + int_str

    # get fractional digits (no scientific notation)
    frac_str = format(frac_part, 'f').split('.')[1]
    # remove trailing zeros
    frac_str = frac_str.rstrip('0')
    if not frac_str:
        return sign + int_str

    # Use comma as decimal separator (Turkish style)
    return sign + int_str + ',' + frac_str


# register filter for Jinja templates
app.jinja_env.filters['thousands'] = format_thousands

# Basit audit log helper
def log_audit(cur, user_id, action, entity_type, entity_id=None, details=None):
    """Kritik işlemleri audit_logs tablosuna yazar. Hata alırsa çağıran transaction ile beraber geri alınır."""
    cur.execute(
        """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, action, entity_type, entity_id, json.dumps(details) if details else None)
    )


def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, email, password_hash, full_name FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user 

@app.route("/ping")
def ping():
    return jsonify({"status": "ok"}), 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = get_user_by_email(email)
        if user and check_password_hash(user[2], password):
            session['user_id'] = user[0]
            session['user_name'] = user[3]
            return redirect(url_for('dashboard'))
        else:
            flash('E-posta veya şifre hatalı.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/project/new', methods=['GET', 'POST'])
def new_project():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        project_type = request.form['project_type']
        floors = request.form['floors']
        flats = request.form['flats']

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO projects (name, address, project_type, total_floors, total_flats)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (name, address, project_type, floors, flats))
        project_id = cur.fetchone()[0]  # Proje ID'yi al
        log_audit(cur, session.get('user_id'), 'project_create', 'project', project_id,
                  {'name': name, 'type': project_type, 'floors': floors, 'flats': flats})
        conn.commit()
        cur.close()
        conn.close()

        flash('Proje başarıyla eklendi. Şimdi daireleri tanımlayabilirsiniz.', 'success')
        return redirect(url_for('manage_flats', project_id=project_id)) # YENİ YÖNLENDİRME

    return render_template('project_new.html')



@app.route('/project/<int:project_id>/manage_flats', methods=['GET', 'POST'])
def manage_flats(project_id):
    """
    Bir projedeki daireleri akıllıca yönetir (ekler, günceller, sahibi olmayanları siler).
    Mevcut ve satılmış daireleri korur.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
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

        except Exception as e:
            conn.rollback()
            flash(f'Daireler güncellenirken bir hata oluştu: {e}', 'danger')
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
        
    except Exception as e:
        flash(f'Veri alınırken bir hata oluştu: {e}', 'danger')
        project_name = "Bilinmeyen Proje"
        existing_flats = []
    finally:
        cur.close()
        conn.close()

    return render_template('manage_flats.html', 
                           project_id=project_id, 
                           project_name=project_name,
                           existing_flats=existing_flats)



@app.route('/expenses', methods=['GET'])
def list_expenses():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    expenses_data, petty_cash_items = [], []
    project_name = ""
    total_project_expense, total_paid_project, total_remaining_due = Decimal(0), Decimal(0), Decimal(0)
    total_petty_cash_expense = Decimal(0)
    supplier_list = []
    large_titles, petty_titles = [], []

    try:
        project_id = int(project_id_str)

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
            cur.execute(expenses_sql, tuple(expenses_params))
            expenses_raw = cur.fetchall()

        # --- DÜZELTİLEN KISIM: Küçük giderler (Python Tarafında %100 Garanti Sıralama) ---
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

            # Python ile kesin sıralama: x[3] senin girdiğin "Harcama Tarihi"dir (expense_date)
            is_reverse = (pc_order == 'desc')
            if pc_sort == 'amount':
                # Tutara göre sırala
                petty_cash_items = sorted(raw_petty, key=lambda x: (x[2], x[3], x[0]), reverse=is_reverse)
            else:
                # Kullanıcının Girdiği Tarihe Göre (x[3]) sırala
                petty_cash_items = sorted(raw_petty, key=lambda x: (x[3], x[0]), reverse=is_reverse)
        else:
            petty_cash_items = []

        # Küçük Giderlerin Toplamını Hesapla
        total_petty_cash_expense = sum(item[2] for item in petty_cash_items)

        # Özet Hesaplamaları
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
        total_planned_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        total_petty_cash = cur.fetchone()[0]
        total_project_expense = total_planned_expense + total_petty_cash
        cur.execute("SELECT COALESCE(SUM(es.paid_amount), 0) FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id WHERE e.project_id = %s", (project_id,))
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
            
            # Gider taksitlerini Python tarafında sıralayalım
            expense_dict['installments'].sort(key=lambda x: x['due_date'])
            expenses_data.append(expense_dict)
            
    except Exception as e:
        flash(f"Giderler listelenirken bir hata oluştu: {e}", "danger")
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
                           user_name=session.get('user_name'))


# Bu fonksiyon artık ana giriş noktasıdır.
@app.route('/expenses/select')
def select_project_for_expenses():
    return redirect(url_for('list_expenses'))

# app.py içine eklenecek/değiştirilecek fonksiyonlar

# YARDIMCI FONKSİYON: Gider ödemelerini taksitlerle eşleştirir.
def reconcile_expense_payments(cur, expense_id):
    """
    Bir gidere ait tüm taksitlerin ödenen tutarlarını,
    sadece GEÇERLİ ödemelere (nakit veya durumu 'karşılıksız' olmayan çekler)
    göre baştan hesaplar.
    """
    # 1. Gider için yapılan GEÇERLİ ödemelerin toplamını al
    cur.execute("""
        SELECT COALESCE(SUM(sp.amount), 0)
        FROM supplier_payments sp
        LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
        WHERE sp.expense_id = %s AND (sp.payment_method = 'nakit' OR oc.status != 'karsiliksiz')
    """, (expense_id,))
    total_valid_paid = cur.fetchone()[0]

    # 2. İlgili giderin tüm taksitlerini sıfırla
    cur.execute("UPDATE expense_schedule SET paid_amount = 0, is_paid = FALSE WHERE expense_id = %s", (expense_id,))
    
    # 3. Hesaplanan doğru tutarı taksitlere baştan dağıt
    amount_to_distribute = total_valid_paid
    cur.execute("SELECT id, amount FROM expense_schedule WHERE expense_id = %s ORDER BY due_date ASC", (expense_id,))
    installments = cur.fetchall()
    for inst_id, total_amount in installments:
        if amount_to_distribute <= 0: break
        payment_for_this_inst = min(amount_to_distribute, total_amount)
        is_paid = (payment_for_this_inst >= total_amount)
        cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = %s WHERE id = %s", (payment_for_this_inst, is_paid, inst_id))
        amount_to_distribute -= payment_for_this_inst

@app.route('/supplier_payment/<int:payment_id>/edit', methods=['GET', 'POST'])
def edit_supplier_payment(payment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_connection()
    cur = conn.cursor()
    
    # --- FORM GÖNDERİLDİĞİNDE (POST İSTEĞİ) ---
    if request.method == 'POST':
        try:
            amount_str = request.form.get('amount').replace('.', '').replace(',', '.')
            amount = Decimal(amount_str)
            payment_date = request.form.get('payment_date')
            description = request.form.get('description')
            
            cur.execute("SELECT expense_id, check_id FROM supplier_payments WHERE id = %s", (payment_id,))
            result = cur.fetchone()
            if not result:
                raise ValueError("Güncellenecek ödeme kaydı bulunamadı.")
            expense_id, check_id = result

            cur.execute("UPDATE supplier_payments SET amount=%s, payment_date=%s, description=%s WHERE id=%s",
                        (amount, payment_date, description, payment_id))

            if check_id:
                check_due_date = request.form.get('check_due_date')
                cur.execute("UPDATE outgoing_checks SET amount = %s, issue_date = %s, due_date = %s WHERE id = %s",
                            (amount, payment_date, check_due_date, check_id))

            reconcile_expense_payments(cur, expense_id)
            
            conn.commit()
            flash('Gider ödemesi başarıyla güncellendi.', 'success')
            
            cur.execute("SELECT project_id FROM expenses WHERE id = %s", (expense_id,))
            project_id = cur.fetchone()[0]
            return redirect(url_for('list_expenses', project_id=project_id))

        except Exception as e:
            conn.rollback()
            flash(f'Güncelleme sırasında hata: {e}', 'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('edit_supplier_payment', payment_id=payment_id))

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

    except Exception as e:
        flash(f'Ödeme bilgileri alınırken hata oluştu: {e}', 'danger')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()
        conn.close()

    return render_template('edit_supplier_payment.html', payment=payment, payment_id=payment_id, user_name=session.get('user_name'))

@app.route('/supplier_payment/<int:payment_id>/delete', methods=['POST'])
def delete_supplier_payment(payment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT expense_id FROM supplier_payments WHERE id = %s", (payment_id,))
        expense_id = cur.fetchone()[0]
        cur.execute("DELETE FROM supplier_payments WHERE id = %s", (payment_id,))
        reconcile_expense_payments(cur, expense_id)
        log_audit(cur, session.get('user_id'), 'supplier_payment_delete', 'supplier_payment', payment_id,
                  {'expense_id': expense_id})
        conn.commit()
        flash('Gider ödemesi silindi ve ilgili taksitler güncellendi.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Ödeme silinirken hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()
    project_id = request.form.get('project_id')
    return redirect(url_for('list_expenses', project_id=project_id))


# new_supplier_payment fonksiyonu

@app.route('/supplier_payment/new', methods=['GET', 'POST'])
def new_supplier_payment():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            supplier_id = int(request.form.get('supplier_id'))
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date')
            project_id = int(request.form.get('project_id')) # Ödemeyi bir projeyle ilişkilendirmek için
            description = request.form.get('description', 'Tedarikçi Ödemesi')

            if not all([supplier_id, payment_amount, payment_date_str, project_id]):
                raise ValueError("Tüm zorunlu alanlar doldurulmalıdır.")

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

        except Exception as e:
            if conn: conn.rollback()
            flash(f'Gider ödemesi kaydedilirken bir hata oluştu: {e}', 'danger')
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
def add_expense(project_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            title = request.form['title']
            description = request.form.get('description', '')
            
            # Tedarikçi işlemleri
            supplier_option = request.form.get('supplier_option')
            supplier_id = None
            if supplier_option == 'new':
                new_supplier_name = request.form.get('new_supplier_name')
                if not new_supplier_name: raise ValueError("Yeni tedarikçi adı zorunludur.")
                cur.execute(
                    "INSERT INTO suppliers (name, project_id, category) VALUES (%s, %s, %s) RETURNING id",
                    (new_supplier_name, project_id, request.form.get('new_supplier_category'))
                )
                supplier_id = cur.fetchone()[0]
            else:
                supplier_id_val = request.form.get('supplier_id')
                if not supplier_id_val: raise ValueError("Lütfen bir tedarikçi seçin.")
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
                raise ValueError("En az bir taksit/ödeme girişi yapılmalıdır.")

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

        except Exception as e:
            conn.rollback()
            flash(f'Gider eklenirken bir hata oluştu: {e}', 'danger')
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
def pay_expense_installment(installment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = request.form.get('next') or request.args.get('next')
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date')
            payment_method = request.form.get('payment_method', 'nakit')
            description = request.form.get('description', '')
            
            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()
            
            cur.execute("SELECT expense_id, amount, paid_amount FROM expense_schedule WHERE id = %s", (installment_id,))
            inst = cur.fetchone()
            if not inst: raise ValueError("Ödeme yapılacak taksit bulunamadı.")
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
                if not due_date_str: raise ValueError("Çek için vade tarihi zorunludur.")
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

        except Exception as e:
            if conn: conn.rollback()
            flash(f"Ödeme kaydedilirken bir hata oluştu: {e}", "danger")
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



# assign_flat_owner fonksiyonu

@app.route('/assign_flat_owner', methods=['GET', 'POST'])
def assign_flat_owner():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            project_id = int(request.form.get('project_id'))
            flat_id = int(request.form.get('flat_id'))
            customer_option = request.form.get('customer_option')
            customer_id = None

            if customer_option == 'new':
                new_first_name = request.form.get('new_first_name')
                new_last_name = request.form.get('new_last_name')
                if not new_first_name or not new_last_name:
                    flash('Yeni müşteri için ad ve soyad zorunludur.', 'danger')
                    return redirect(url_for('assign_flat_owner'))
                cur.execute(
                    "INSERT INTO customers (first_name, last_name, phone, national_id) VALUES (%s, %s, %s, %s) RETURNING id",
                    (new_first_name, new_last_name, request.form.get('new_phone'), request.form.get('new_national_id'))
                )
                customer_id = cur.fetchone()[0]
                conn.commit()
                flash(f'Yeni müşteri "{new_first_name} {new_last_name}" başarıyla eklendi.', 'info')
            
            elif customer_option == 'existing':
                customer_id = request.form.get('customer_id')
                if not customer_id:
                    flash('Lütfen mevcut bir müşteri seçin.', 'danger')
                    return redirect(url_for('assign_flat_owner'))
            
            if customer_id and flat_id:
                cur.execute("UPDATE flats SET owner_id = %s WHERE id = %s", (customer_id, flat_id))
                conn.commit()
                flash('Daire sahibi başarıyla atandı!', 'success')

                # Proje türünü kontrol et
                cur.execute("SELECT project_type FROM projects WHERE id = %s", (project_id,))
                project_type = cur.fetchone()[0]

                if project_type == 'normal':
                    # Eğer proje "normal" ise, ödeme planı sayfasına yönlendir
                    flash('Şimdi bu daire için bir ödeme planı oluşturabilirsiniz.', 'info')
                    return redirect(url_for('manage_payment_plan', flat_id=flat_id))
                else:
                    # Eğer proje "kooperatif" ise, aynı sayfada kal
                    return redirect(url_for('assign_flat_owner'))
            else:
                flash('Gerekli tüm bilgiler sağlanmadı.', 'danger')
            
            return redirect(url_for('assign_flat_owner'))

        except Exception as e:
            conn.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')
        finally:
            cur.close()
            conn.close()

    try:
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        projects = cur.fetchall()
        cur.execute("""
            SELECT f.id, pr.name, f.flat_no, f.floor, c.first_name, c.last_name, f.owner_id, 
                   f.block_name, c.phone, c.national_id
            FROM flats f
            JOIN projects pr ON f.project_id = pr.id
            LEFT JOIN customers c ON f.owner_id = c.id
            ORDER BY pr.name, f.block_name, f.flat_no
        """)
        flats_data = cur.fetchall()
        cur.execute("SELECT id, first_name, last_name FROM customers ORDER BY first_name, last_name")
        customers = cur.fetchall()
    except Exception as e:
        flash(f'Veri çekilirken bir hata oluştu: {e}', 'danger')
        projects, flats_data, customers = [], [], []
    finally:
        if not conn.closed:
            cur.close()
            conn.close()
    
    return render_template('assign_flat_owner.html',
                           projects=projects,
                           flats_data=flats_data,
                           customers=customers,
                           user_name=session.get('user_name'))

# GÜNCELLENMİŞ FONKSİYON: debt_status
# app.py içindeki debt_status fonksiyonunu bulun ve güncelleyin

@app.route('/debts')
def debt_status():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    projects_data = []
    project_filter = request.args.get('project_id', type=int)
    flat_filter = request.args.get('flat_id', type=int)
    selected_project_name = None
    selected_flat_desc = None

    try:
        # Proje listesi (filtre datalisti için)
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        all_projects = cur.fetchall()

        # Adım 1: Daireleri çek
        flats_sql = """
            SELECT 
                f.id, p.name, p.project_type, f.block_name, f.floor, f.flat_no,
                c.first_name, c.last_name, f.total_price, p.id as project_id 
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            JOIN customers c ON f.owner_id = c.id
            WHERE f.owner_id IS NOT NULL
        """
        flats_params = []
        if project_filter:
            flats_sql += " AND f.project_id = %s"
            flats_params.append(project_filter)
        if flat_filter:
            flats_sql += " AND f.id = %s"
            flats_params.append(flat_filter)
        flats_sql += " ORDER BY p.name, f.block_name, f.floor, f.flat_no"
        cur.execute(flats_sql, tuple(flats_params))
        owned_flats = cur.fetchall()

        if project_filter:
            cur.execute("SELECT name FROM projects WHERE id = %s", (project_filter,))
            row = cur.fetchone()
            selected_project_name = row[0] if row else None
        if flat_filter:
            cur.execute("SELECT block_name, floor, flat_no FROM flats WHERE id = %s", (flat_filter,))
            row = cur.fetchone()
            if row:
                selected_flat_desc = f"Blok: {row[0] or 'N/A'}, Kat: {row[1]}, No: {row[2]}"

        # Proje bazlı toplamlar (filtre olsa bile tamamını göstermek için)
        cur.execute("""
            SELECT f.project_id, COALESCE(SUM(f.total_price), 0)
            FROM flats f
            WHERE f.owner_id IS NOT NULL
            GROUP BY f.project_id
        """)
        project_income_all = dict(cur.fetchall())

        cur.execute("""
            SELECT f.project_id, COALESCE(SUM(p.amount), 0) as total_paid
            FROM payments p
            JOIN flats f ON p.flat_id = f.id
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE f.owner_id IS NOT NULL AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
            GROUP BY f.project_id
        """)
        project_paid_all = dict(cur.fetchall())

        # Adım 2: TÜM taksitleri çek ve TARİHE GÖRE (ve ID'ye göre) SIRALA
        # *** DÜZELTME: ORDER BY kısmına ', id ASC' eklendi. Bu, karışıklığı önler. ***
        inst_sql = """
            SELECT flat_id, due_date, amount, is_paid, paid_amount, id 
            FROM installment_schedule 
        """
        inst_params = []
        if flat_filter:
            inst_sql += " WHERE flat_id = %s"
            inst_params.append(flat_filter)
        elif project_filter:
            inst_sql += " WHERE flat_id IN (SELECT id FROM flats WHERE project_id = %s AND owner_id IS NOT NULL)"
            inst_params.append(project_filter)
        inst_sql += " ORDER BY flat_id, due_date ASC, id ASC"
        cur.execute(inst_sql, tuple(inst_params))
        all_installments_raw = cur.fetchall()
        installments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_installments_raw, key=lambda x: x[0])}

        # Adım 3: Ödemeleri topla (Aynı kalıyor)
        pay_sql = """
            SELECT p.flat_id, COALESCE(SUM(p.amount), 0) as total_paid 
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE p.payment_method = 'nakit' OR c.status = 'tahsil_edildi'
        """
        pay_params = []
        if flat_filter:
            pay_sql += " AND p.flat_id = %s"
            pay_params.append(flat_filter)
        elif project_filter:
            pay_sql += " AND p.flat_id IN (SELECT id FROM flats WHERE project_id = %s AND owner_id IS NOT NULL)"
            pay_params.append(project_filter)
        pay_sql += " GROUP BY p.flat_id"
        cur.execute(pay_sql, tuple(pay_params))
        total_payments_by_flat = dict(cur.fetchall())

        # Adım 3.5: Ödeme geçmişini çek (Aynı kalıyor)
        pay_hist_sql = """
            SELECT 
                p.id, p.flat_id, p.payment_date, p.description, p.amount, p.payment_method,
                c.status, c.bank_name, c.check_number, c.due_date, p.check_id
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
        """
        pay_hist_params = []
        if flat_filter:
            pay_hist_sql += " WHERE p.flat_id = %s"
            pay_hist_params.append(flat_filter)
        elif project_filter:
            pay_hist_sql += " WHERE p.flat_id IN (SELECT id FROM flats WHERE project_id = %s AND owner_id IS NOT NULL)"
            pay_hist_params.append(project_filter)
        pay_hist_sql += " ORDER BY p.flat_id, p.payment_date DESC, p.id DESC"
        cur.execute(pay_hist_sql, tuple(pay_hist_params))
        all_payments_raw = cur.fetchall()
        payments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_payments_raw, key=lambda x: x[1])}

        # Adım 4: Verileri birleştir
        flats_list = []
        today = date.today()
        for flat_id, project_name, project_type, block_name, floor, flat_no, first_name, last_name, total_price, project_id_val in owned_flats:
            total_paid = total_payments_by_flat.get(flat_id, Decimal(0))
            flat_dict = {
                'flat_id': flat_id,
                'project_id': project_id_val,
                'project_name': project_name,
                'project_type': project_type,
                'customer_name': f"{first_name} {last_name}",
                'flat_details': f"Blok: {block_name or 'N/A'}, Kat: {floor}, No: {flat_no}",
                'total_paid': total_paid,
                'flat_total_price': total_price or Decimal(0),
                'remaining_debt': (total_price or Decimal(0)) - total_paid,
                'installments': [],
                'payments': payments_by_flat.get(flat_id, [])
            }
            
            if project_type == 'normal':
                current_installments = installments_by_flat.get(flat_id, [])
                for inst_flat_id, due_date, total_amount, is_paid, paid_amount, inst_id in current_installments:
                    paid_amount = paid_amount or Decimal(0)
                    status, css_class = ("Ödendi", "table-success") if is_paid else (f"Kısmen Ödendi", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
                    flat_dict['installments'].append({
                        'id': inst_id, 'due_date': due_date, 'total_amount': total_amount, 
                        'remaining_installment_due': total_amount - paid_amount,
                        'status': status, 'css_class': css_class
                    })
                # *** YENİ EKLENECEK SATIR ***
                # Veritabanı sıralaması yetmezse, Python ile zorla tarihe göre sırala
                flat_dict['installments'].sort(key=lambda x: x['due_date'])
            flats_list.append(flat_dict)

        # Adım 5: Gruplama (Aynı kalıyor)
        for key_tuple, group in groupby(flats_list, key=lambda x: (x['project_id'], x['project_name'])):
            group_list = list(group)
            project_id_key, project_name_key = key_tuple
            
            total_project_income = Decimal(project_income_all.get(project_id_key, 0))
            total_project_paid = Decimal(project_paid_all.get(project_id_key, 0))
            total_project_remaining = total_project_income - total_project_paid

            projects_data.append({
                'project_id': project_id_key,
                'project_name': project_name_key,
                'project_type': group_list[0]['project_type'],
                'flats': group_list,
                'total_project_income': total_project_income,
                'total_project_paid': total_project_paid,
                'total_project_remaining': total_project_remaining
            })

    except Exception as e:
        flash(f'Borç durumu sayfası yüklenirken bir hata oluştu: {e}', 'danger')
        print(f"DEBTS PAGE ERROR: {e}")
        projects_data = []
        all_projects = []
    finally:
        cur.close()
        conn.close()

    return render_template('debts.html',
                           projects_data=projects_data,
                           all_projects=all_projects,
                           selected_project_id=project_filter,
                           selected_project_name=selected_project_name,
                           selected_flat_id=flat_filter,
                           selected_flat_desc=selected_flat_desc,
                           user_name=session.get('user_name'))

@app.route('/project/<int:project_id>/edit', methods=['GET', 'POST'])
def edit_project(project_id):
    """Mevcut bir projeyi düzenler ve daire sayısı artarsa daire ekleme sayfasına yönlendirir."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

        except Exception as e:
            conn.rollback()
            flash(f'Proje güncellenirken bir hata oluştu: {e}', 'danger')
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
def delete_project(project_id):
    """Bir projeyi ve ona bağlı tüm verileri (ilişkili tüm çekler dahil) siler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
    except Exception as e:
        conn.rollback()
        flash(f'Proje silinirken bir hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('dashboard'))

@app.route('/delete_flat_owner_data', methods=['POST'])
def delete_flat_owner_data():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Yetkisiz erişim'}), 401

    data = request.get_json()
    flat_id = data.get('flat_id')

    if not flat_id:
        return jsonify({'success': False, 'message': 'Daire ID eksik.'}), 400

    conn = get_connection()
    cur = conn.cursor()

    try:
        # 1. Daireye bağlı GELİR çeklerinin ID'lerini bul
        cur.execute("SELECT check_id FROM payments WHERE flat_id = %s AND check_id IS NOT NULL", (flat_id,))
        check_ids_to_delete = [row[0] for row in cur.fetchall()]

        # 2. Dairenin ödeme planını (taksitlerini) sil
        cur.execute("DELETE FROM installment_schedule WHERE flat_id = %s", (flat_id,))
        
        # 3. Dairenin ödeme kayıtlarını sil
        cur.execute("DELETE FROM payments WHERE flat_id = %s", (flat_id,))

        # 4. Bulunan çekleri `checks` tablosundan sil
        if check_ids_to_delete:
            cur.execute("DELETE FROM checks WHERE id IN %s", (tuple(check_ids_to_delete),))

        # 5. Dairenin sahibini ve finansal bilgilerini sıfırla
        cur.execute("""
            UPDATE flats
            SET owner_id = NULL, total_price = NULL, total_installments = NULL
            WHERE id = %s
        """, (flat_id,))
        
        conn.commit()
        return jsonify({'success': True, 'message': 'Daire sahibi ve ilgili tüm finansal veriler (çekler dahil) başarıyla sıfırlandı.'})

    except Exception as e:
        conn.rollback()
        print(f"HATA: Daire sahibi ve ilgili veriler silinirken hata oluştu: {e}")
        return jsonify({'success': False, 'message': f'Veri silinirken hata oluştu: {str(e)}'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/customers')
def list_customers():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    
    search_query = request.args.get('search', '').strip()

    try:
        # 1. Müşterileri Çek
        sql_customers = "SELECT id, first_name, last_name, phone, national_id FROM customers"
        params = []
        if search_query:
            sql_customers += " WHERE first_name ILIKE %s OR last_name ILIKE %s"
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        sql_customers += " ORDER BY first_name, last_name"
        
        cur.execute(sql_customers, params)
        customers_raw = cur.fetchall()

        # 2. Daireleri ve Proje Bilgilerini Çek
        cur.execute("""
            SELECT 
                f.owner_id, f.id as flat_id, p.name as project_name, p.project_type,
                f.block_name, f.floor, f.flat_no, f.total_price 
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            WHERE f.owner_id IS NOT NULL
        """)
        flats_raw = cur.fetchall()

        # 3. Gerçekleşen Ödemelerin Toplamını Çek (Sadece Nakit ve Tahsil Edilmiş Çekler)
        cur.execute("""
            SELECT p.flat_id, COALESCE(SUM(p.amount), 0) as total_paid 
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE p.payment_method = 'nakit' OR c.status = 'tahsil_edildi'
            GROUP BY p.flat_id
        """)
        total_paid_dict = dict(cur.fetchall())

        # 3.5. TÜM ÖDEME GEÇMİŞİNİ ÇEK (Yeni Eklendi)
        cur.execute("""
            SELECT 
                p.flat_id, p.id, p.payment_date, p.description, p.amount, p.payment_method,
                c.status, c.bank_name, c.check_number, c.due_date
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            ORDER BY p.flat_id, p.payment_date DESC
        """)
        from itertools import groupby
        all_payments_raw = cur.fetchall()
        payments_history_dict = {k: list(v) for k, v in groupby(all_payments_raw, key=lambda x: x[0])}

        # 4. Taksit Planlarını Çek
        cur.execute("""
            SELECT flat_id, due_date, amount, is_paid, paid_amount 
            FROM installment_schedule 
            ORDER BY flat_id, due_date ASC, id ASC
        """)
        installments_raw = cur.fetchall()
        installments_dict = {k: list(v) for k, v in groupby(installments_raw, key=lambda x: x[0])}

        # 5. Verileri İç İçe Paketle
        customers_data = []
        for c_id, f_name, l_name, phone, nat_id in customers_raw:
            customer_flats = []
            my_flats = [f for f in flats_raw if f[0] == c_id]
            
            for _, flat_id, p_name, p_type, block, floor, flat_no, t_price in my_flats:
                total_paid = total_paid_dict.get(flat_id, Decimal(0))
                total_price = t_price or Decimal(0)
                
                flat_installments = []
                if p_type == 'normal':
                    for _, d_date, i_amount, is_paid, p_amount in installments_dict.get(flat_id, []):
                        flat_installments.append({
                            'due_date': d_date,
                            'amount': i_amount,
                            'is_paid': is_paid,
                            'paid_amount': p_amount or Decimal(0)
                        })

                # Ödeme geçmişini formatla ve pakete ekle (Yeni Eklendi)
                flat_payments = []
                for _, p_id, p_date, p_desc, p_amount, p_method, p_status, p_bank, p_no, p_due in payments_history_dict.get(flat_id, []):
                    flat_payments.append({
                        'id': p_id, 'date': p_date, 'desc': p_desc, 'amount': p_amount,
                        'method': p_method, 'status': p_status, 'bank': p_bank, 'no': p_no, 'due': p_due
                    })

                customer_flats.append({
                    'flat_id': flat_id,
                    'project_name': p_name,
                    'project_type': p_type,
                    'details': f"Blok: {block or '-'}, Kat: {floor}, No: {flat_no}",
                    'total_price': total_price,
                    'total_paid': total_paid,
                    'remaining': total_price - total_paid,
                    'installments': flat_installments,
                    'payments': flat_payments # Şablona Gönderilen Kısım
                })

            customers_data.append({
                'id': c_id,
                'name': f"{f_name} {l_name}",
                'phone': phone,
                'national_id': nat_id,
                'flats': customer_flats
            })

    except Exception as e:
        flash(f"Müşteriler yüklenirken hata oluştu: {e}", "danger")
        customers_data = []
    finally:
        cur.close()
        conn.close()

    return render_template('customers.html', customers_data=customers_data, user_name=session.get('user_name'))

@app.route('/checks')
def list_checks():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
        cur.execute(in_sql, tuple(in_params))
        incoming_checks = cur.fetchall()

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
        cur.execute(out_sql, tuple(out_params))
        outgoing_checks = cur.fetchall()

    except Exception as e:
        flash(f"Çekler listelenirken bir hata oluştu: {e}", "danger")
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
                           incoming_parties=incoming_parties, outgoing_parties=outgoing_parties
                           )


# update_check_status fonksiyonu
@app.route('/check/update_status', methods=['POST'])
def update_check_status():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    except Exception as e:
        conn.rollback()
        flash(f"Çek durumu güncellenirken bir hata oluştu: {e}", "danger")
    finally:
        cur.close()
        conn.close()

    next_url = request.form.get('next') or request.referrer or url_for('debt_status')
    return redirect(next_url)


@app.route('/reports/cooperative/select', methods=['GET', 'POST'])
def select_project_for_coop_report():
    """Kooperatif raporu için proje seçim sayfası."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
    # Sadece kooperatif projelerini listele
    cur.execute("SELECT id, name FROM projects WHERE project_type = 'cooperative' ORDER BY name")
    projects = cur.fetchall()
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
def cooperative_report(project_id, year, month):
    """Belirli bir kooperatif projesinin aylık finansal raporunu gösterir."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
        cur.execute("SELECT name, total_flats FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()
        report_data['project_name'] = project_info[0]
        
        # Üye sayısını (sahibi olan daire sayısı) al
        cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s AND owner_id IS NOT NULL", (project_id,))
        member_count = cur.fetchone()[0]
        report_data['member_count'] = member_count

        # 1. Önceki Aydan Devreden Bakiyeyi Hesapla
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id
            WHERE f.project_id = %s AND p.payment_date < %s
        """, (project_id, start_date))
        total_income_before = cur.fetchone()[0]
        
        # Önceki aydan devreden giderler her iki tablodan toplanıyor
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s AND expense_date < %s", (project_id, start_date))
        total_large_expense_before = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date < %s", (project_id, start_date))
        total_petty_cash_before = cur.fetchone()[0]
        total_expense_before = total_large_expense_before + total_petty_cash_before
        
        previous_balance = total_income_before - total_expense_before
        report_data['previous_balance'] = previous_balance

        # 2. Bu Ayın Gelir ve Giderlerini Hesapla
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id
            WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s
        """, (project_id, start_date, end_date))
        current_income = cur.fetchone()[0]
        report_data['current_income'] = current_income
        
        # Rapor ayına ait giderler her iki tablodan toplanıyor
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        current_large_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        current_petty_cash_expense = cur.fetchone()[0]
        current_expense = current_large_expense + current_petty_cash_expense
        report_data['current_expense'] = current_expense
        
        # 3. Ay Sonu Bakiyesini Hesapla
        end_of_month_balance = previous_balance + current_income - current_expense
        report_data['end_of_month_balance'] = end_of_month_balance

        # 4. Detaylı listeler için verileri çek
        cur.execute("""
            SELECT p.payment_date, c.first_name || ' ' || c.last_name, p.description, p.amount 
            FROM payments p 
            JOIN flats f ON p.flat_id = f.id 
            JOIN customers c ON f.owner_id = c.id
            WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s ORDER BY p.payment_date
        """, (project_id, start_date, end_date))
        income_details = cur.fetchall()
        report_data['income_details'] = income_details

        # Gider detayları listesi her iki tablodan birleştirilip tarihe göre sıralanıyor
        cur.execute("SELECT expense_date, title, description, amount FROM expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        large_expense_details = cur.fetchall()
        cur.execute("SELECT expense_date, title, description, amount FROM petty_cash_expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        petty_cash_details = cur.fetchall()
        
        expense_details = large_expense_details + petty_cash_details
        expense_details.sort(key=lambda x: x[0]) 
        report_data['expense_details'] = expense_details
        
        month_name = turkish_months.get(start_date.month, "")
        report_data['report_period'] = f"{month_name} {start_date.year}"

    except Exception as e:
        flash(f"Rapor oluşturulurken bir hata oluştu: {e}", "danger")
    finally:
        cur.close()
        conn.close()

    return render_template('coop_report.html', 
                           report_data=report_data,
                           user_name=session.get('user_name'))


# app.py'deki mevcut project_transactions fonksiyonunu bu kodla değiştirin

@app.route('/project/<int:project_id>/transactions', methods=['GET'])
def project_transactions(project_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    except Exception as e:
        flash(f"İşlem listesi alınırken hata oluştu: {e}", "danger")
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
def project_overview(project_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
        cur.execute("SELECT name, project_type FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()
        project_name, project_type = project_info

        # --- ÖZET KARTI HESAPLAMALARI (Doğru haliyle) ---
        # --- ÖZET KARTI HESAPLAMALARI ---
        if project_type == 'normal':
            # GELİR: Sadece nakit ve tahsil edilmiş çekleri topla
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
        
        # GİDER: Sadece nakit ve 'odendi' durumundaki firma çeklerini topla
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

        # --- GELİR TABLOSU VERİLERİ (Önceki doğru haliyle) ---
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
                total_cleared_payments = total_cash + total_cleared_check
                total_valid_payments = total_cleared_payments + total_portfolio_check
                paid_so_far = max(0, total_valid_payments - (cumulative_amount - amount))
                paid_this_installment = min(amount, paid_so_far)
                if paid_this_installment >= amount:
                    if cumulative_amount <= total_cash: status, status_class, payment_method = "Ödendi", "bg-success", "nakit"
                    elif cumulative_amount <= total_cleared_payments: status, status_class, payment_method = "Ödendi", "bg-success", "çek"
                    else: status, status_class, payment_method = "Çek Portföyde", "bg-warning text-dark", "çek"
                elif paid_this_installment > 0: 
                    status, status_class = "Kısmen Ödendi", "bg-info text-dark"
                    payment_method = None # Ödenmemişse yöntem olmaz
                else: 
                    status, status_class = ("Gecikmiş", "bg-danger") if due_date < today else ("Bekleniyor", "bg-secondary")
                    payment_method = None # Ödenmemişse yöntem olmaz
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
        
        # --- GİDER TABLOSU VERİLERİ (DÜZELTİLMİŞ MANTIK İLE) ---
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
            
            total_cleared_payments = total_cash + total_cleared_check
            total_valid_payments = total_cleared_payments + total_portfolio_check
            paid_so_far = max(0, total_valid_payments - (cumulative_amount - amount))
            paid_this_installment = min(amount, paid_so_far)
            
            # *** HATANIN ÇÖZÜMÜ BURADA ***
            if paid_this_installment >= amount: # Taksit tam ödenmiş
                if cumulative_amount <= total_cash: 
                    status, status_class, payment_method = "Ödendi", "bg-success", "nakit"
                elif cumulative_amount <= total_cleared_payments: 
                    status, status_class, payment_method = "Ödendi", "bg-success", "çek"
                else: 
                    status, status_class, payment_method = "Çek Verildi", "bg-warning text-dark", "çek"
            elif paid_this_installment > 0: # Kısmen ödenmiş
                status, status_class = "Kısmen Ödendi", "bg-info text-dark"
                payment_method = None # Düzeltme: Yöntemi sıfırla
            else: # Hiç ödenmemiş
                status, status_class = ("Gecikmiş", "bg-danger") if due_date < today else ("Bekleniyor", "bg-secondary")
                payment_method = None # Düzeltme: Yöntemi sıfırla
                
            expense_items.append({'date': due_date, 'description': title, 'party': sup_name or "Belirtilmemiş", 'details': 'Planlı Gider', 'amount': amount, 'status': status, 'status_class': status_class, 'payment_method': payment_method})

        # Küçük Nakit Giderler
        cur.execute("SELECT expense_date, title, amount, description FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        for expense_date, title, amount, desc in cur.fetchall():
            expense_items.append({'date': expense_date, 'description': title, 'party': 'Kasa', 'details': desc or 'Küçük Gider', 'amount': amount, 'status': 'Ödendi', 'status_class': 'bg-success', 'payment_method': 'nakit'})

        # Tarih filtresi
        if start_date_str:
            start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] >= start_date_obj]
            expense_items = [e for e in expense_items if e['date'] >= start_date_obj]
        if end_date_str:
            end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] <= end_date_obj]
            expense_items = [e for e in expense_items if e['date'] <= end_date_obj]

        # Parti listeleri (tarih filtresinden sonra)
        income_parties = sorted({i['party'] for i in income_items})
        expense_parties = sorted({e['party'] for e in expense_items})

        # Ek filtreler - Gelir
        if income_party:
            lp = income_party.lower()
            income_items = [i for i in income_items if lp in i['party'].lower()]
        if income_method != 'all':
            income_items = [i for i in income_items if (i['payment_method'] or '').lower() == income_method.lower()]
        if income_status != 'all':
            ls = income_status.lower()
            income_items = [i for i in income_items if i['status'].lower().startswith(ls)]

        # Ek filtreler - Gider
        if expense_party:
            le = expense_party.lower()
            expense_items = [e for e in expense_items if le in (e['party'] or '').lower()]
        if expense_method != 'all':
            expense_items = [e for e in expense_items if (e['payment_method'] or '').lower() == expense_method.lower()]
        if expense_status != 'all':
            ls = expense_status.lower()
            expense_items = [e for e in expense_items if e['status'].lower().startswith(ls)]

        # Sıralama
        is_reverse = (order == 'desc')
        key_to_sort = 'party' if sort_by == 'party' else 'date'
        income_items.sort(key=lambda x: x.get(key_to_sort, today if key_to_sort == 'date' else ''), reverse=is_reverse)
        expense_items.sort(key=lambda x: x.get('date', today), reverse=is_reverse)

    except Exception as e:
        flash(f"Proje genel bakışı oluşturulurken hata: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    
    return render_template('project_overview.html', project_id=project_id, project_name=project_name, 
    income_items=income_items, expense_items=expense_items, total_paid_income=total_paid_income, total_unpaid_income=total_unpaid_income, total_paid_expense=total_paid_expense, total_unpaid_expense=total_unpaid_expense, user_name=session.get('user_name'), project_type=project_type, start_date=start_date_str, end_date=end_date_str, sort_by=sort_by, order=order,
    income_party=income_party, income_method=income_method, income_status=income_status,
    expense_party=expense_party, expense_method=expense_method, expense_status=expense_status,
    income_parties=income_parties, expense_parties=expense_parties)


@app.route('/project/<int:project_id>/petty_cash/add', methods=['POST'])
def add_petty_cash(project_id):
    """Bir projeye yeni bir küçük gider ekler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        title = request.form.get('petty_cash_title')
        amount = Decimal(request.form.get('petty_cash_amount').replace('.', '').replace(',', '.'))
        expense_date = request.form.get('petty_cash_date')
        description = request.form.get('petty_cash_description')
        next_url = request.form.get('next')

        if not all([title, amount, expense_date]):
            raise ValueError("Başlık, Tutar ve Tarih alanları zorunludur.")
        
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO petty_cash_expenses (project_id, title, amount, expense_date, description) VALUES (%s, %s, %s, %s, %s)",
            (project_id, title, amount, expense_date, description)
        )
        conn.commit()
        flash("Küçük gider başarıyla eklendi.", "success")

    except Exception as e:
        flash(f"Küçük gider eklenirken bir hata oluştu: {e}", "danger")
    finally:
        if 'conn' in locals() and conn:
            cur.close()
            conn.close()

    return redirect(next_url or url_for('list_expenses', project_id=project_id))


@app.route('/petty_cash/<int:item_id>/delete', methods=['POST'])
def delete_petty_cash(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    project_id = request.form.get('project_id')
    next_url = request.form.get('next')
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM petty_cash_expenses WHERE id = %s", (item_id,))
        conn.commit()
        flash('Küçük gider kaydı silindi.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Küçük gider silinirken hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()

    if project_id:
        return redirect(next_url or url_for('list_expenses', project_id=project_id))
    return redirect(next_url or url_for('dashboard'))


@app.route('/petty_cash/<int:item_id>/edit', methods=['GET', 'POST'])
def edit_petty_cash(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title')
        amount_raw = request.form.get('amount') or '0'
        amount = Decimal(amount_raw.replace('.', '').replace(',', '.'))
        expense_date = request.form.get('expense_date')
        description = request.form.get('description')
        project_id = request.args.get('project_id') or request.form.get('project_id')
        next_url = request.form.get('next')
        try:
            cur.execute("UPDATE petty_cash_expenses SET title=%s, amount=%s, expense_date=%s, description=%s WHERE id=%s",
                        (title, amount, expense_date, description, item_id))
            conn.commit()
            flash('Küçük gider güncellendi.', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Güncelleme sırasında hata oluştu: {e}', 'danger')
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
def manage_expense_plan(expense_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = request.form.get('next') or request.args.get('next')
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
                raise ValueError("En az bir taksit girilmelidir.")

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

        except Exception as e:
            conn.rollback()
            flash(f'Gider planı güncellenirken hata: {e}', 'danger')
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
def delete_expense(expense_id):
    """Belirli bir gideri ve varsa ilişkili çekini veritabanından siler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

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
    except Exception as e:
        conn.rollback()
        flash(f'Gider silinirken bir hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()

    if project_id:
        return redirect(url_for('list_expenses', project_id=project_id))
    else:
        return redirect(url_for('dashboard'))


@app.route('/audit-logs')
def audit_logs():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    except Exception as e:
        flash(f"Loglar yüklenirken hata: {e}", "danger")
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

# get_flats_for_project fonksiyonu

@app.route('/api/project/<int:project_id>/flats')
def get_flats_for_project(project_id):
    """
    Bir projeye ait, sahibi olan daireleri listeler.
    Daire metninde blok, kat, no ve sahip ismini içerir.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Yetkisiz erişim'}), 401

    conn = get_connection()
    cur = conn.cursor()
    
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
    cur.close()
    conn.close()

    flats = [{
        'id': f[0], 
        'text': f"Blok: {f[4] or 'N/A'}, Kat: {f[2]}, No: {f[1]}  —  ({f[5]} {f[6]})"
    } for f in flats_raw]
    
    return jsonify(flats)


# app.py dosyasındaki manage_payment_plan fonksiyonunu bununla değiştirin:

@app.route('/flat/<int:flat_id>/manage_plan', methods=['GET', 'POST'])
def manage_payment_plan(flat_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = request.form.get('next') or request.args.get('next')
            plan_json = request.form.get('plan_json')

            # --- 1) Form verisini oku ---
            rows_raw = []
            if plan_json:
                # JSON üzerinden (öncelikli)
                rows_raw = json.loads(plan_json)
            else:
                # Geriye dönük uyumluluk: liste alanlarından toparla
                due_dates = request.form.getlist('due_date[]')
                amounts_str = request.form.getlist('amount[]')
                rows_raw = [{'due_date': d, 'amount': a} for d, a in zip_longest(due_dates, amounts_str, fillvalue="")]

            # --- 2) Satırları parse et ve temizle ---
            parsed_rows = []
            for row in rows_raw:
                date_str = (row.get('due_date') or "").strip()
                amount_str = (row.get('amount') or "").strip()
                if not date_str or not amount_str:
                    continue
                try:
                    due_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    raise ValueError(f"Geçersiz tarih: {date_str}")

                cleaned = amount_str.replace(' ', '').replace('.', '').replace(',', '.')
                try:
                    amount = Decimal(cleaned)
                except Exception:
                    raise ValueError(f"Geçersiz tutar: {amount_str}")

                parsed_rows.append((due_date, amount))

            if not parsed_rows:
                raise ValueError("En az bir taksit girilmelidir.")

            # Tarihe göre sırala (ID'lere güvenmek yerine)
            parsed_rows.sort(key=lambda x: x[0])

            # 3) Mevcut planı tamamen temizle ve yeniden yaz
            cur.execute("DELETE FROM installment_schedule WHERE flat_id = %s", (flat_id,))

            # 4) Yeni planı ekle
            for due_date, amount in parsed_rows:
                cur.execute("""
                    INSERT INTO installment_schedule (flat_id, due_date, amount, is_paid, paid_amount)
                    VALUES (%s, %s, %s, FALSE, 0)
                """, (flat_id, due_date, amount))

            # 5) Ödemeleri taksitlere yeniden dağıt
            reconcile_customer_payments(cur, flat_id)

            # 6) Daire toplamlarını güncelle
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM installment_schedule WHERE flat_id = %s", (flat_id,))
            total_price = cur.fetchone()[0]
            cur.execute("SELECT COUNT(id) FROM installment_schedule WHERE flat_id = %s", (flat_id,))
            total_installments = cur.fetchone()[0]
            cur.execute("UPDATE flats SET total_price = %s, total_installments = %s WHERE id = %s",
                        (total_price, total_installments, flat_id))

            # Audit log
            log_audit(
                cur,
                session.get('user_id'),
                'plan_update',
                'payment_plan',
                flat_id,
                {'rows': len(parsed_rows), 'total_price': float(total_price), 'total_installments': total_installments}
            )

            conn.commit()
            flash('Ödeme planı başarıyla güncellendi.', 'success')
            return redirect(next_url or url_for('debt_status'))

        except Exception as e:
            conn.rollback()
            flash(f'Plan güncellenirken hata oluştu: {e}', 'danger')
            return redirect(next_url or url_for('manage_payment_plan', flat_id=flat_id))
        finally:
            cur.close()
            conn.close()

    # GET İsteği (Sayfa Açılışı)
    # *** KRİTİK NOKTA: Buraya 'id' alanını ekledik. (Index 3 olacak) ***
    cur.execute("""
        SELECT due_date, amount, is_paid, id 
        FROM installment_schedule 
        WHERE flat_id = %s 
        ORDER BY due_date ASC, id ASC
    """, (flat_id,))
    existing_installments = cur.fetchall()
    
    cur.execute("SELECT p.name, f.block_name, f.floor, f.flat_no FROM flats f JOIN projects p ON f.project_id = p.id WHERE f.id = %s", (flat_id,))
    flat_info = cur.fetchone()
    
    cur.close()
    conn.close()
    return render_template('manage_payment_plan.html',
                           flat_id=flat_id,
                           flat_info=flat_info,
                           existing_installments=existing_installments,
                           next_url=request.args.get('next', ''))

# print_debt_statement fonksiyonu

@app.route('/flat/<int:flat_id>/print')
def print_debt_statement(flat_id):
    """Belirli bir dairenin borç dökümünü yazdırma için hazırlar."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    try:
        # 1. Daire, proje ve müşteri bilgilerini çek
        cur.execute("""
            SELECT p.name, c.first_name, c.last_name, f.floor, f.flat_no, f.total_price, f.block_name
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            JOIN customers c ON f.owner_id = c.id
            WHERE f.id = %s
        """, (flat_id,))
        statement_info_raw = cur.fetchone()

        if not statement_info_raw:
            flash('Döküm alınacak daire bulunamadı.', 'danger')
            return redirect(url_for('debt_status'))

        # 2. Daireye ait tüm taksitleri çek (paid_amount ile birlikte)
        cur.execute("""
            SELECT due_date, amount, is_paid, paid_amount
            FROM installment_schedule
            WHERE flat_id = %s
            ORDER BY due_date
        """, (flat_id,))
        installments_raw = cur.fetchall()

        # 3. Daire için yapılan toplam ödemeyi çek
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE flat_id = %s", (flat_id,))
        total_paid = cur.fetchone()[0]

        # 3.5. Ödeme kayıtlarını çek
        cur.execute("""
            SELECT p.payment_date, p.description, p.amount, p.payment_method,
                   c.status, c.bank_name, c.check_number, c.due_date
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE p.flat_id = %s
            ORDER BY p.payment_date DESC, p.id DESC
        """, (flat_id,))
        payments_raw = cur.fetchall()

        # 4. Verileri işleyip şablon için hazırla
        statement_data = {
            'project_name': statement_info_raw[0],
            'customer_name': f"{statement_info_raw[1]} {statement_info_raw[2]}",
            'flat_details': f"Blok: {statement_info_raw[6] or 'N/A'}, Kat: {statement_info_raw[3]}, No: {statement_info_raw[4]}",
            'flat_total_price': statement_info_raw[5] or 0,
            'total_paid': total_paid,
            'remaining_debt': (statement_info_raw[5] or 0) - total_paid,
            'print_date': date.today(),
            'installments': [],
            'payments': []
        }

        today = date.today()
        for due_date, amount, is_paid, paid_amount in installments_raw:
            status = ""
            if is_paid:
                status = "Ödendi"
            elif paid_amount > 0:
                status = f"Kısmen Ödendi ({paid_amount} ₺)"
            elif due_date < today:
                status = "Gecikmiş"
            else:
                status = "Bekleniyor"
            
            statement_data['installments'].append({
                'due_date': due_date,
                'amount': amount,
                'status': status,
                'remaining_due': amount - paid_amount
            })

        for pay_date, desc, amount, method, chk_status, bank, chk_no, chk_due in payments_raw:
            statement_data['payments'].append({
                'payment_date': pay_date,
                'description': desc,
                'amount': amount,
                'method': method,
                'check_status': chk_status,
                'bank': bank,
                'check_number': chk_no,
                'check_due': chk_due
            })

        return render_template('print_statement.html', data=statement_data)

    except Exception as e:
        flash(f"Döküm oluşturulurken bir hata oluştu: {e}", "danger")
        return redirect(url_for('debt_status'))
    finally:
        cur.close()
        conn.close()


# list_payments fonksiyonu

@app.route('/payments')
def list_payments():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Filtreleme parametreleri
    project = request.args.get('project')
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    customer_id = request.args.get('customer_id')
    
    # Sıralama parametreleri
    sort_by = request.args.get('sort_by', 'tarih')
    order = request.args.get('order', 'desc')
    
    sortable_columns = {
        'proje': 'pr.name', 'musteri': 'c.last_name',
        'tarih': 'p.payment_date', 'tutar': 'p.amount'
    }
    order_by_column = sortable_columns.get(sort_by, 'p.payment_date')
    if order not in ['asc', 'desc']: order = 'desc'

    sql = """
        SELECT p.id, pr.name, c.first_name, c.last_name, f.flat_no, f.floor,
               p.installment, p.amount, p.payment_date, f.block_name, p.payment_method, p.description
        FROM payments p
        JOIN flats f ON p.flat_id = f.id
        JOIN customers c ON f.owner_id = c.id
        JOIN projects pr ON f.project_id = pr.id
    """
    filters, params = [], []

    # Filtreleri sorguya ekle
    if project:
        filters.append("pr.name = %s")
        params.append(project)
    if start:
        filters.append("p.payment_date >= %s")
        params.append(start)
    if end:
        filters.append("p.payment_date <= %s")
        params.append(end)
    if customer_id:
        filters.append("c.id = %s")
        params.append(customer_id)

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += f" ORDER BY {order_by_column} {order.upper()}"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, tuple(params))
    payments = cur.fetchall()
    
    cur.execute("SELECT name FROM projects ORDER BY name")
    all_projects = [r[0] for r in cur.fetchall()]
    
    cur.execute("SELECT id, first_name, last_name FROM customers ORDER BY first_name, last_name")
    all_customers = cur.fetchall()
    
    cur.close()
    conn.close()

    return render_template('payments.html',
                           payments=payments,
                           all_projects=all_projects,
                           all_customers=all_customers,
                           selected_project=project,
                           selected_customer_id=customer_id,
                           start_date=start,
                           end_date=end,
                           sort_by=sort_by,
                           order=order,
                           user_name=session.get('user_name'))
@app.route('/reports')
def reports():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    try:
        cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
        projects = cur.fetchall()
        project_summaries = []
        monthly_series = {}
        check_series = {}
        projection_series = {}
        overdue_items = {}
        month_boxes = {}

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

                # --- Aylık gelir-gider (son 12 ay) ---
                labels, income_series, expense_series = [], [], []
                for offset in range(11, -1, -1):
                    month_start = add_months(start_month, -offset)
                    month_end = add_months(month_start, 1)
                    labels.append(month_start.strftime("%b %Y"))

                    # Gerçekleşen gelir (nakit + tahsil edilen çek)
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
                        (project_id, month_start, month_end)
                    )
                    income_series.append(float(cur.fetchone()[0]))

                    # Gerçekleşen gider (nakit + ödenmiş çek)
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
                        (project_id, month_start, month_end)
                    )
                    paid_large = float(cur.fetchone()[0])

                    cur.execute(
                        """
                        SELECT COALESCE(SUM(amount), 0)
                        FROM petty_cash_expenses
                        WHERE project_id = %s
                          AND expense_date >= %s AND expense_date < %s
                        """,
                        (project_id, month_start, month_end)
                    )
                    petty_paid = float(cur.fetchone()[0])

                    expense_series.append(paid_large + petty_paid)

                net_series = [inc - exp for inc, exp in zip(income_series, expense_series)]
                monthly_series[project_id] = {
                    'labels': labels,
                    'income': income_series,
                    'expense': expense_series,
                    'net': net_series
                }

                # --- Aylık çek durumu (son 12 ay) ---
                chk_labels = labels  # aynı etiketler
                in_port, in_clear, out_given, out_paid = [], [], [], []
                for offset in range(11, -1, -1):
                    month_start = add_months(start_month, -offset)
                    month_end = add_months(month_start, 1)

                    cur.execute(
                        """
                        SELECT COALESCE(SUM(c.amount), 0)
                        FROM checks c
                        JOIN payments p ON c.id = p.check_id
                        JOIN flats f ON p.flat_id = f.id
                        WHERE f.project_id = %s AND c.status = 'portfoyde'
                          AND c.due_date >= %s AND c.due_date < %s
                        """,
                        (project_id, month_start, month_end)
                    )
                    in_port.append(float(cur.fetchone()[0]))

                    cur.execute(
                        """
                        SELECT COALESCE(SUM(c.amount), 0)
                        FROM checks c
                        JOIN payments p ON c.id = p.check_id
                        JOIN flats f ON p.flat_id = f.id
                        WHERE f.project_id = %s AND c.status = 'tahsil_edildi'
                          AND c.due_date >= %s AND c.due_date < %s
                        """,
                        (project_id, month_start, month_end)
                    )
                    in_clear.append(float(cur.fetchone()[0]))

                    cur.execute(
                        """
                        SELECT COALESCE(SUM(oc.amount), 0)
                        FROM outgoing_checks oc
                        JOIN supplier_payments sp ON oc.id = sp.check_id
                        JOIN expenses e ON sp.expense_id = e.id
                        WHERE e.project_id = %s AND oc.status = 'verildi'
                          AND oc.due_date >= %s AND oc.due_date < %s
                        """,
                        (project_id, month_start, month_end)
                    )
                    out_given.append(float(cur.fetchone()[0]))

                    cur.execute(
                        """
                        SELECT COALESCE(SUM(oc.amount), 0)
                        FROM outgoing_checks oc
                        JOIN supplier_payments sp ON oc.id = sp.check_id
                        JOIN expenses e ON sp.expense_id = e.id
                        WHERE e.project_id = %s AND oc.status = 'odendi'
                          AND oc.due_date >= %s AND oc.due_date < %s
                        """,
                        (project_id, month_start, month_end)
                    )
                    out_paid.append(float(cur.fetchone()[0]))

                check_series[project_id] = {
                    'labels': chk_labels,
                    'incoming_portfolio': in_port,
                    'incoming_cleared': in_clear,
                    'outgoing_given': out_given,
                    'outgoing_paid': out_paid
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

    except Exception as e:
        import traceback
        print("REPORTS ERROR:", e)
        traceback.print_exc()
        flash(f"Raporlar oluşturulurken hata oluştu: {e}", "danger")
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
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

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

    except Exception as e:
        flash(f"Dashboard yüklenirken bir hata oluştu: {e}", "danger")
        print(f"DASHBOARD HATASI: {e}")
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
def monthly_payments_api():
    """Son 12 ayın aylık toplam ödemelerini JSON formatında döndürür."""
    if 'user_id' not in session:
        return jsonify({'error': 'Yetkisiz erişim'}), 401

    conn = get_connection()
    cur = conn.cursor()

    # Son 12 ayın verisini çekmek için veritabanına özel bir sorgu gönder
    # Bu sorgu, her ayın başlangıcını ve o aydaki toplam ödemeyi hesaplar.
    # `DATE_TRUNC('month', ...)` fonksiyonu tarihi ayın ilk gününe yuvarlar
    cur.execute("""
        SELECT 
            DATE_TRUNC('month', payment_date)::DATE AS month, 
            SUM(amount) AS total_amount
        FROM 
            payments
        WHERE 
            payment_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months'
        GROUP BY 
            month
        ORDER BY 
            month;
    """)
    
    results = cur.fetchall()
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

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/payment/new', defaults={'installment_id': None}, methods=['GET', 'POST'])
@app.route('/payment/new/<int:installment_id>', methods=['GET', 'POST'])
def new_payment(installment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        # ... (POST kısmı aynı kalıyor, DOKUNMAYIN) ...
        try:
            next_url = request.form.get('next') or request.args.get('next')
            flat_id = int(request.form.get('flat_id'))
            # Formdan gelen formatlı sayıyı temizleyerek Decimal'e çeviriyoruz
            payment_amount_str = request.form.get('amount')
            # Önce noktaları kaldır, sonra virgülü noktaya çevir (1.234,56 -> 1234.56)
            payment_amount = Decimal(payment_amount_str.replace('.', '').replace(',', '.'))
            payment_date_str = request.form.get('payment_date') 
            description = request.form.get('description', '')
            payment_method = request.form.get('payment_method', 'nakit')

            if not all([flat_id, payment_amount_str, payment_date_str]): # Tutar için orijinal stringi kontrol et
                flash('Lütfen tüm zorunlu alanları doldurun.', 'danger')
                 # Hata durumunda hangi sayfaya yönlendireceğimizi belirle
                redirect_url = url_for('new_payment', installment_id=installment_id) if installment_id else url_for('new_payment', project_id=request.form.get('project_id'), flat_id=flat_id)
                return redirect(redirect_url)


            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()

            cur.execute("SELECT owner_id FROM flats WHERE id = %s", (flat_id,))
            owner_result = cur.fetchone()
            if not owner_result or not owner_result[0]:
                 flash('Seçilen dairenin sahibi bulunamadı veya atanmamış.', 'danger')
                 redirect_url = url_for('new_payment', installment_id=installment_id) if installment_id else url_for('new_payment', project_id=request.form.get('project_id'), flat_id=flat_id)
                 return redirect(redirect_url)
            customer_id = owner_result[0]


            if payment_method == 'nakit':
                cur.execute(
                    "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method) VALUES (%s, %s, %s, %s, %s)",
                    (flat_id, payment_amount, payment_date, description or 'Nakit Ödeme', 'nakit')
                )
                 # Sadece 'normal' projelerde taksit eşleştirme yap
                cur.execute("SELECT project_type FROM projects p JOIN flats f ON p.id=f.project_id WHERE f.id = %s", (flat_id,))
                project_type = cur.fetchone()[0]
                if project_type == 'normal':
                    reconcile_customer_payments(cur, flat_id)
                flash(f'{format_thousands(payment_amount)} ₺ tutarındaki nakit ödeme kaydedildi.', 'success')


            elif payment_method == 'çek':
                due_date_str = request.form.get('check_due_date')
                if not due_date_str:
                    flash('Çek ödemesi için Vade Tarihi zorunludur.', 'danger')
                    redirect_url = url_for('new_payment', installment_id=installment_id) if installment_id else url_for('new_payment', project_id=request.form.get('project_id'), flat_id=flat_id)
                    return redirect(redirect_url)

                
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                cur.execute(
                    "INSERT INTO checks (customer_id, bank_name, check_number, amount, issue_date, due_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (customer_id, request.form.get('check_bank_name'), request.form.get('check_number'), payment_amount, payment_date, due_date)
                )
                check_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method, check_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (flat_id, payment_amount, payment_date, description or f'Çek Ödemesi', 'çek', check_id)
                )

                # Çek kaydedilince de 'normal' proje ise taksitleri eşleştir
                cur.execute("SELECT project_type FROM projects p JOIN flats f ON p.id=f.project_id WHERE f.id = %s", (flat_id,))
                project_type = cur.fetchone()[0]
                if project_type == 'normal':
                   reconcile_customer_payments(cur, flat_id) # Çek kaydedildiğinde de eşleştirme yap
                   flash(f'Çek başarıyla portföye eklendi. Tahsil edildiğinde borçtan düşülecektir.', 'info')
                else:
                    flash(f'Çek başarıyla portföye eklendi. Tahsil edildiğinde borca yansıtılacaktır.', 'success')


            log_audit(cur, session.get('user_id'), 'payment_create', 'payment', None,
                      {'flat_id': flat_id, 'amount': float(payment_amount), 'method': payment_method, 'check_id': locals().get('check_id'), 'date': payment_date_str})

            conn.commit()
            return redirect(next_url or url_for('debt_status'))
        except Exception as e:
            conn.rollback()
            flash(f'Ödeme kaydedilirken bir hata oluştu: {e}', 'danger')
            # Hata durumunda hangi sayfaya yönlendireceğimizi belirle
            redirect_kwargs = {}
            if installment_id:
                redirect_kwargs['installment_id'] = installment_id
            else:
                redirect_kwargs['project_id'] = request.form.get('project_id')
                redirect_kwargs['flat_id'] = request.form.get('flat_id')
            if next_url:
                redirect_kwargs['next'] = next_url
            redirect_url = url_for('new_payment', **redirect_kwargs)
            return redirect(redirect_url)

        finally:
            cur.close()
            conn.close()

    # --- GET isteği ---
    installment_info = None
    coop_payment_info = None # *** YENİ: Kooperatif bilgisi için değişken ***
    flats_for_project = []
    
    # URL'den gelen proje ve daire ID'lerini al (query parametreleri)
    project_id_query = request.args.get('project_id', type=int)
    flat_id_query = request.args.get('flat_id', type=int)

    # Önce taksit ID'sine göre bilgileri çekmeye çalış (Normal proje)
    if installment_id:
        cur.execute("""
            SELECT s.id, s.due_date, s.amount, s.paid_amount, f.id as flat_id, f.block_name, f.floor, f.flat_no,
                   p.id as project_id, p.name as project_name, c.first_name, c.last_name
            FROM installment_schedule s JOIN flats f ON s.flat_id = f.id JOIN projects p ON f.project_id = p.id JOIN customers c ON f.owner_id = c.id
            WHERE s.id = %s
        """, (installment_id,))
        inst = cur.fetchone()
        if inst:
            installment_info = {
                'id': inst[0], 'due_date': inst[1], 'total_amount': inst[2], 'paid_amount': inst[3] or Decimal(0),
                'remaining_due': (inst[2] - (inst[3] or Decimal(0))), 'flat_id': inst[4],
                'flat_details': f"Blok: {inst[5] or 'N/A'}, Kat: {inst[6]}, No: {inst[7]}", 'project_id': inst[8],
                'project_name': inst[9], 'customer_name': f"{inst[10]} {inst[11]}"
            }
            # İlgili projenin dairelerini yükle
            cur.execute("""
                SELECT f.id, f.block_name, f.floor, f.flat_no, c.first_name, c.last_name FROM flats f JOIN customers c ON f.owner_id = c.id
                WHERE f.project_id = %s ORDER BY f.block_name, f.flat_no
            """, (installment_info['project_id'],))
            flats_for_project = [{'id': row[0], 'text': f"Blok: {row[1]}, Kat: {row[2]}, No: {row[3]} - ({row[4]} {row[5]})"} for row in cur.fetchall()]
    
    # *** YENİ: Eğer taksit ID'si yoksa ama query parametreleri varsa (Kooperatif) ***
    elif project_id_query and flat_id_query:
         cur.execute("""
            SELECT p.name as project_name, f.block_name, f.floor, f.flat_no, c.first_name, c.last_name
            FROM flats f 
            JOIN projects p ON f.project_id = p.id 
            JOIN customers c ON f.owner_id = c.id 
            WHERE f.id = %s AND p.id = %s
        """, (flat_id_query, project_id_query))
         coop_data = cur.fetchone()
         if coop_data:
             coop_payment_info = {
                 'project_id': project_id_query,
                 'flat_id': flat_id_query,
                 'project_name': coop_data[0],
                 'flat_details': f"Blok: {coop_data[1] or 'N/A'}, Kat: {coop_data[2]}, No: {coop_data[3]}",
                 'customer_name': f"{coop_data[4]} {coop_data[5]}"
             }
             # İlgili projenin dairelerini yükle
             cur.execute("""
                SELECT f.id, f.block_name, f.floor, f.flat_no, c.first_name, c.last_name FROM flats f JOIN customers c ON f.owner_id = c.id
                WHERE f.project_id = %s ORDER BY f.block_name, f.flat_no
            """, (project_id_query,))
             flats_for_project = [{'id': row[0], 'text': f"Blok: {row[1]}, Kat: {row[2]}, No: {row[3]} - ({row[4]} {row[5]})"} for row in cur.fetchall()]


    # Genel proje listesini her zaman çek
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('new_payment.html', 
                           projects=projects,
                           installment_info=installment_info,
                           coop_payment_info=coop_payment_info, # *** YENİ: Şablona gönder ***
                           flats_for_project=flats_for_project,
                           user_name=session.get('user_name'))

# YENİ YARDIMCI FONKSİYON: Gider ödemelerini taksitlerle eşleştirir.
# 2. Tedarikçi (Gider) Ödemelerini Eşitleme Fonksiyonu
def reconcile_supplier_payments(cur, expense_id):
    """
    Bir gidere (expense) ait tüm taksitlerin ödenen tutarlarını,
    sadece GEÇERLİ ödemelere (nakit veya durumu 'odendi' olan çekler)
    göre baştan hesaplar.
    """
    # 1. Bu gider için yapılan GEÇERLİ ödemelerin (Nakit + Ödenen Çekler) toplamını al
    cur.execute("""
        SELECT COALESCE(SUM(sp.amount), 0)
        FROM supplier_payments sp
        LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
        WHERE sp.expense_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')
    """, (expense_id,))
    total_valid_paid = cur.fetchone()[0]

    # 2. Bu giderin tüm taksitlerini (schedule) sıfırla
    cur.execute("UPDATE expense_schedule SET paid_amount = 0, is_paid = FALSE WHERE expense_id = %s", (expense_id,))
    
    # 3. Geçerli toplam ödemeyi taksitlere vadesi en eskiden başlayarak baştan dağıt
    amount_to_distribute = total_valid_paid
    
    # *** KRİTİK DÜZELTME: Sıralamaya 'id ASC' eklendi. ***
    cur.execute("SELECT id, amount FROM expense_schedule WHERE expense_id = %s ORDER BY due_date ASC, id ASC", (expense_id,))
    installments = cur.fetchall()

    for inst_id, total_amount in installments:
        if amount_to_distribute <= 0:
            break
        payment_for_this_inst = min(amount_to_distribute, total_amount)
        is_paid = (payment_for_this_inst >= total_amount)
        cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = %s WHERE id = %s",
                    (payment_for_this_inst, is_paid, inst_id))
        amount_to_distribute -= payment_for_this_inst
                           
# GÜNCELLENMİŞ FONKSİYON: delete_payment
@app.route('/payment/<int:payment_id>/delete', methods=['POST'])
def delete_payment(payment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Silmeden önce flat_id ve check_id'yi al
        cur.execute("SELECT flat_id, check_id FROM payments WHERE id = %s", (payment_id,))
        payment_info = cur.fetchone()
        if not payment_info:
            flash('Silinecek ödeme kaydı bulunamadı.', 'warning')
            return redirect(url_for('debt_status'))
        
        flat_id, check_id = payment_info

        # Önce ödeme kaydını sil
        cur.execute("DELETE FROM payments WHERE id = %s", (payment_id,))
        
        # Eğer ilişkili bir çek varsa, onu da sil
        if check_id:
            cur.execute("DELETE FROM checks WHERE id = %s", (check_id,))
        
        # Taksit durumlarını yeniden hesapla
        reconcile_customer_payments(cur, flat_id)

        log_audit(cur, session.get('user_id'), 'payment_delete', 'payment', payment_id,
                  {'flat_id': flat_id, 'check_id': check_id})
        
        conn.commit()
        flash('Ödeme kaydı silindi ve taksit durumu güncellendi.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Ödeme silinirken hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()
    # debt_status sayfasına geri dön
    return redirect(url_for('debt_status'))

# YENİ YARDIMCI FONKSİYON: Müşteri ödemelerini taksitlerle eşleştirir.
# YARDIMCI FONKSİYON: Müşteri ödemelerini taksitlerle eşleştirir.
# 1. Müşteri Ödemelerini Eşitleme Fonksiyonu
def reconcile_customer_payments(cur, flat_id):
    """
    Bir daireye ait tüm taksitlerin ödenen tutarlarını,
    sadece GEÇERLİ ödemelere (nakit veya durumu 'tahsil_edildi' olan çekler)
    göre baştan hesaplar.
    """
    # 1. Toplam geçerli ödemeyi al
    cur.execute("""
        SELECT COALESCE(SUM(p.amount), 0)
        FROM payments p
        LEFT JOIN checks c ON p.check_id = c.id
        WHERE p.flat_id = %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
    """, (flat_id,))
    total_valid_paid = cur.fetchone()[0]

    # 2. Taksitleri sıfırla
    cur.execute("UPDATE installment_schedule SET paid_amount = 0, is_paid = FALSE WHERE flat_id = %s", (flat_id,))
    
    # 3. Parayı dağıt (Sıralama: Tarih artan, ID artan)
    amount_to_distribute = total_valid_paid
    # *** DÜZELTME: ORDER BY due_date ASC, id ASC ***
    cur.execute("SELECT id, amount FROM installment_schedule WHERE flat_id = %s ORDER BY due_date ASC, id ASC", (flat_id,))
    installments = cur.fetchall()

    for inst_id, total_amount in installments:
        if amount_to_distribute <= 0:
            break
        payment_for_this_inst = min(amount_to_distribute, total_amount)
        is_paid = (payment_for_this_inst >= total_amount)
        cur.execute("UPDATE installment_schedule SET paid_amount = %s, is_paid = %s WHERE id = %s",
                    (payment_for_this_inst, is_paid, inst_id))
        amount_to_distribute -= payment_for_this_inst

# GÜNCELLENMİŞ FONKSİYON: edit_payment
@app.route('/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
def edit_payment(payment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            next_url = request.form.get('next') or request.args.get('next')
            amount = Decimal(request.form.get('amount').replace('.', '').replace(',', '.'))
            payment_date_str = request.form.get('payment_date')
            description = request.form.get('description')

            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date() if payment_date_str else None

            # Güncellemeden önce flat_id ve check_id'yi al
            cur.execute("SELECT flat_id, check_id FROM payments WHERE id = %s", (payment_id,))
            payment_info = cur.fetchone()
            if not payment_info:
                flash('Düzenlenecek ödeme kaydı bulunamadı.', 'warning')
                return redirect(url_for('debt_status'))
            
            flat_id, check_id = payment_info
            
            # payments tablosunu güncelle
            cur.execute("UPDATE payments SET amount=%s, payment_date=%s, description=%s WHERE id=%s",
                        (amount, payment_date, description, payment_id))

            # Eğer ilişkili bir çek varsa, checks tablosunu da güncelle
            if check_id:
                check_due_date = request.form.get('check_due_date')
                check_bank_name = request.form.get('check_bank_name')
                check_number = request.form.get('check_number')
                parsed_due = datetime.strptime(check_due_date, '%Y-%m-%d').date() if check_due_date else None
                
                cur.execute("UPDATE checks SET due_date=%s, bank_name=%s, check_number=%s, amount=%s, issue_date=%s WHERE id=%s",
                            (parsed_due, check_bank_name or None, check_number or None, amount, payment_date, check_id))

            # Taksit durumlarını yeniden hesapla
            reconcile_customer_payments(cur, flat_id)

            conn.commit()
            flash('Ödeme bilgileri güncellendi ve taksit durumu yeniden hesaplandı.', 'success')
            return redirect(next_url or url_for('debt_status'))
        except Exception as e:
            conn.rollback()
            flash(f'Güncelleme sırasında hata oluştu: {e}', 'danger')
            return redirect(next_url or url_for('debt_status'))
        finally:
            cur.close()
            conn.close()

    # GET: fetch payment details for form
    cur.execute("""
        SELECT p.id, pr.name, c.first_name, c.last_name, f.flat_no, f.floor, 
               p.amount, p.payment_date, p.description, p.payment_method, p.check_id 
        FROM payments p 
        JOIN flats f ON p.flat_id = f.id 
        JOIN customers c ON f.owner_id = c.id 
        JOIN projects pr ON f.project_id = pr.id 
        WHERE p.id = %s
    """, (payment_id,))
    row = cur.fetchone()
    if not row:
        flash('Ödeme kaydı bulunamadı.', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('debt_status'))
    
    payment = {
        'id': row[0], 'project_name': row[1], 'customer_name': f"{row[2]} {row[3]}",
        'flat_desc': f"No: {row[4]} - Kat: {row[5]}", 'amount': row[6],
        'payment_date': row[7].isoformat() if row[7] else '', 'description': row[8],
        'payment_method': row[9], 'check_id': row[10]
    }
    
    if payment.get('check_id'):
        cur.execute("SELECT due_date, bank_name, check_number FROM checks WHERE id = %s", (payment['check_id'],))
        c_row = cur.fetchone()
        if c_row:
            payment['check_due_date'] = c_row[0].isoformat() if c_row[0] else ''
            payment['check_bank_name'] = c_row[1]
            payment['check_number'] = c_row[2]
            
    cur.close()
    conn.close()
    return render_template('edit_payment.html', payment=payment, user_name=session.get('user_name'), next_url=request.args.get('next', ''))



if __name__ == '__main__':
    app.run(debug=True)

