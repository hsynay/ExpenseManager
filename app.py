from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify 
from dateutil.relativedelta import relativedelta 
from flask import Flask, render_template, request, redirect, url_for, session, flash
from db import get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from parser import parse_whatsapp_message
import os
from datetime import datetime
from itertools import groupby
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
        conn.commit()
        cur.close()
        conn.close()

        flash('Proje başarıyla eklendi. Şimdi daireleri tanımlayabilirsiniz.', 'success')
        return redirect(url_for('manage_flats', project_id=project_id)) # YENİ YÖNLENDİRME

    return render_template('project_new.html')



# add_flats fonksiyonu

# @app.route('/project/<int:project_id>/flats', methods=['GET', 'POST'])
# def add_flats(project_id):
#     """
#     Bir projedeki tüm daireleri yönetir (CRUD).
#     POST isteğinde, projenin tüm dairelerini siler ve formdan gelen yeni listeyi kaydeder.
#     """
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     conn = get_connection()
#     cur = conn.cursor()

#     if request.method == 'POST':
#         # Formdan gelen tüm daire verilerini listeler halinde al
#         block_names = request.form.getlist('block_name[]')
#         flat_nos = request.form.getlist('flat_no[]')
#         floors = request.form.getlist('floor[]')
#         room_types = request.form.getlist('room_type[]')
        
#         try:
#             # 1. Önce bu projeye ait tüm mevcut daireleri sil (temiz bir başlangıç için)
#             cur.execute("DELETE FROM flats WHERE project_id = %s", (project_id,))
            
#             # 2. Formdan gelen güncel listeyi veritabanına yeniden ekle
#             for block, flat_no, floor, room_type in zip(block_names, flat_nos, floors, room_types):
#                 # Sadece dolu satırların kaydedildiğinden emin ol
#                 if block and flat_no and floor and room_type:
#                     cur.execute("""
#                         INSERT INTO flats (project_id, block_name, flat_no, floor, room_type)
#                         VALUES (%s, %s, %s, %s, %s)
#                     """, (project_id, block.strip(), flat_no, floor, room_type.strip()))
            
#             conn.commit()
#             flash('Daire listesi başarıyla güncellendi.', 'success')
#             return redirect(url_for('assign_flat_owner'))

#         except Exception as e:
#             conn.rollback()
#             flash(f'Daireler güncellenirken bir hata oluştu: {e}', 'danger')
#             return redirect(url_for('add_flats', project_id=project_id))
#         finally:
#             cur.close()
#             conn.close()

#     # GET isteği için: Proje adını ve mevcut daireleri çek
#     try:
#         cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
#         project = cur.fetchone()
#         if not project:
#             flash('Proje bulunamadı.', 'danger')
#             return redirect(url_for('dashboard'))
#         project_name = project[0]
        
#         # Mevcut daireleri forma doldurmak için çek
#         cur.execute("SELECT block_name, flat_no, floor, room_type FROM flats WHERE project_id = %s ORDER BY block_name, floor, flat_no", (project_id,))
#         existing_flats = cur.fetchall()
        
#     except Exception as e:
#         flash(f'Veri alınırken bir hata oluştu: {e}', 'danger')
#         project_name = "Bilinmeyen Proje"
#         existing_flats = []
#     finally:
#         cur.close()
#         conn.close()

#     return render_template('project_flats.html', 
#                            project_id=project_id, 
#                            project_name=project_name,
#                            existing_flats=existing_flats)


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



# app.py'deki MEVCUT list_expenses ve all_expenses fonksiyonlarını SİLİN.
# select_project_for_expenses fonksiyonunu da AŞAĞIDAKİ İLE DEĞİŞTİRİN.

# app.py dosyanızdaki mevcut list_expenses fonksiyonunu bu kodla değiştirin.

# app.py'deki MEVCUT list_expenses ve all_expenses fonksiyonlarını SİLİN.
# select_project_for_expenses fonksiyonunu da AŞAĞIDAKİ İLE DEĞİŞTİRİN.
# app.py'deki MEVCUT list_expenses fonksiyonunu SİLİN ve bu kodla DEĞİŞTİRİN.

@app.route('/expenses', methods=['GET'])
def list_expenses():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    project_id_str = request.args.get('project_id')
    
    # Proje listesini her zaman çekiyoruz
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    all_projects = cur.fetchall()

    # Eğer bir proje ID'si gelmemişse veya 'all' seçilmişse, sadece seçim ekranını göster
    if not project_id_str or project_id_str == 'all':
        cur.close()
        conn.close()
        return render_template('expenses.html',
                               detailed_view=False,
                               all_projects=all_projects,
                               selected_project_id=None,
                               user_name=session.get('user_name'))

    # Eğer bir proje ID'si gelmişse, o projenin detaylarını göster
    try:
        project_id = int(project_id_str)
        expenses_data, petty_cash_items = [], []
        project_name = ""
        total_project_expense, total_paid_project, total_remaining_due = Decimal(0), Decimal(0), Decimal(0)

        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name = cur.fetchone()[0]

        cur.execute("""
            SELECT expense_id, id, payment_date, description, amount, payment_method
            FROM supplier_payments
            WHERE expense_id IN (SELECT id FROM expenses WHERE project_id = %s)
            ORDER BY expense_id, payment_date DESC
        """, (project_id,))
        payments_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

        cur.execute("""
            SELECT expense_id, due_date, amount, is_paid, paid_amount, id as installment_id
            FROM expense_schedule
            WHERE expense_id IN (SELECT id FROM expenses WHERE project_id = %s)
            ORDER BY expense_id, due_date ASC
        """, (project_id,))
        schedules_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

        cur.execute("SELECT e.id, e.title, e.amount, s.name as supplier_name FROM expenses e LEFT JOIN suppliers s ON e.supplier_id = s.id WHERE e.project_id = %s ORDER BY e.id DESC", (project_id,))
        expenses_raw = cur.fetchall()
        
        cur.execute("SELECT id, title, amount, expense_date, description FROM petty_cash_expenses WHERE project_id = %s ORDER BY expense_date DESC", (project_id,))
        petty_cash_items = cur.fetchall()

        # YENİ EKLENEN KISIM: Küçük Giderlerin Toplamını Hesapla
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
        for expense_id_loop, title, total_amount, supplier_name in expenses_raw:
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
            expenses_data.append(expense_dict)
            
    except Exception as e:
        flash(f"Giderler listelenirken bir hata oluştu: {e}", "danger")
        expenses_data, petty_cash_items = [], [] # Hata durumunda listeleri boşalt
    finally:
        cur.close()
        conn.close()

    return render_template('expenses.html',
                           detailed_view=True, project_id=project_id, project_name=project_name,
                           expenses_data=expenses_data, 
                           petty_cash_items=petty_cash_items,
                           total_petty_cash_expense=total_petty_cash_expense, # YENİ: Toplamı şablona gönder
                           total_project_expense=total_project_expense,
                           total_paid_project=total_paid_project,
                           total_remaining_due=total_remaining_due,
                           all_projects=all_projects, selected_project_id=str(project_id),
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
            expense_type = request.form.get('expense_type')
            
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
                supplier_id = int(request.form.get('supplier_id'))

            total_amount = Decimal(0)
            if expense_type == 'planli':
                amounts = [Decimal(a) for a in request.form.getlist('installment_amount[]') if a]
                total_amount = sum(amounts)
            else: # tek_sefer
                total_amount = Decimal(request.form.get('single_amount'))

            cur.execute(
                "INSERT INTO expenses (project_id, title, amount, expense_date, description, supplier_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (project_id, title, total_amount, datetime.now().date(), description, supplier_id)
            )
            expense_id = cur.fetchone()[0]

            if expense_type == 'planli':
                due_dates = request.form.getlist('installment_due_date[]')
                amounts_str = request.form.getlist('installment_amount[]')
                for date_str, amount_str in zip(due_dates, amounts_str):
                    if date_str and amount_str:
                        due_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        amount = Decimal(amount_str)
                        cur.execute(
                            "INSERT INTO expense_schedule (expense_id, due_date, amount) VALUES (%s, %s, %s)",
                            (expense_id, due_date, amount)
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
            
            outgoing_check_id = None
            if payment_method == 'çek':
                due_date_str = request.form.get('check_due_date')
                if not due_date_str: raise ValueError("Çek için vade tarihi zorunludur.")
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                cur.execute(
                    "INSERT INTO outgoing_checks (supplier_id, bank_name, check_number, amount, issue_date, due_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (supplier_id, request.form.get('check_bank_name'), request.form.get('check_number'), payment_amount, payment_date, due_date)
                )
                outgoing_check_id = cur.fetchone()[0]
                flash(f'Çek başarıyla kaydedildi. Taksit, çek ödendiğinde güncellenecektir.', 'info')
            
            else: 
                new_paid_amount = (already_paid or 0) + payment_amount
                is_paid = new_paid_amount >= total_due
                cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = %s WHERE id = %s", (new_paid_amount, is_paid, installment_id))
                flash("Nakit ödeme başarıyla kaydedildi ve borca yansıtıldı.", "success")

            cur.execute(
                "INSERT INTO supplier_payments (expense_id, supplier_id, amount, payment_date, payment_method, description, check_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (expense_id, supplier_id, payment_amount, payment_date, payment_method, description, outgoing_check_id)
            )
            
            conn.commit()
            return redirect(url_for('list_expenses', project_id=project_id))

        except Exception as e:
            if conn: conn.rollback()
            flash(f"Ödeme kaydedilirken bir hata oluştu: {e}", "danger")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('pay_expense_installment', installment_id=installment_id))

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
                           user_name=session.get('user_name'))



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

    try:
        # Adım 1: Sahibi olan TÜM daireleri, proje bilgileriyle birlikte çek (project_id dahil).
        cur.execute("""
            SELECT 
                f.id, p.name, p.project_type, f.block_name, f.floor, f.flat_no,
                c.first_name, c.last_name, f.total_price, p.id as project_id 
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            JOIN customers c ON f.owner_id = c.id
            WHERE f.owner_id IS NOT NULL
            ORDER BY p.name, f.block_name, f.floor, f.flat_no
        """)
        owned_flats = cur.fetchall()

        # Adım 2: TÜM taksitleri bir sözlüğe al (Normal projeler için).
        cur.execute("SELECT flat_id, due_date, amount, is_paid, paid_amount, id FROM installment_schedule ORDER BY flat_id, due_date ASC")
        all_installments_raw = cur.fetchall()
        installments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_installments_raw, key=lambda x: x[0])}

        # Adım 3: TÜM fiili ödemelerin (tahsilatların) toplamını daireye göre grupla.
        cur.execute("SELECT flat_id, COALESCE(SUM(amount), 0) as total_paid FROM payments GROUP BY flat_id")
        total_payments_by_flat = dict(cur.fetchall())

        # Adım 3.5: Tüm fiili ödemeleri listelemek için çek
        cur.execute("SELECT id, flat_id, payment_date, description, amount, payment_method FROM payments ORDER BY flat_id, payment_date DESC")
        all_payments_raw = cur.fetchall()
        payments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_payments_raw, key=lambda x: x[1])}


        # Adım 4: Tüm daire verilerini birleştir
        flats_list = []
        today = date.today()
        # *** DEĞİŞİKLİK: project_id'yi de döngüde alıyoruz ***
        for flat_id, project_name, project_type, block_name, floor, flat_no, first_name, last_name, total_price, project_id_val in owned_flats:
            total_paid = total_payments_by_flat.get(flat_id, Decimal(0))
            flat_dict = {
                'flat_id': flat_id,
                'project_id': project_id_val, # *** YENİ: project_id eklendi ***
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
            
            flats_list.append(flat_dict)

        # Adım 5: Daireleri projelere göre grupla ve her proje için özet hesaplamaları yap
        # *** DEĞİŞİKLİK: Gruplama anahtarı artık (proje_id, proje_adı) tuple'ı ***
        for key_tuple, group in groupby(flats_list, key=lambda x: (x['project_id'], x['project_name'])):
            group_list = list(group)
            project_id_key, project_name_key = key_tuple # Tuple'dan değerleri al
            
            # Proje bazlı özet hesaplamaları (normal projeler için daha anlamlı)
            total_project_income = sum(flat.get('flat_total_price', Decimal(0)) for flat in group_list)
            total_project_paid = sum(flat.get('total_paid', Decimal(0)) for flat in group_list)
            total_project_remaining = total_project_income - total_project_paid

            projects_data.append({
                'project_id': project_id_key, # *** YENİ: Proje ID'sini ekle ***
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
    finally:
        cur.close()
        conn.close()

    return render_template('debts.html', 
                           projects_data=projects_data, 
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
    
    cur.execute("""
        SELECT 
            c.id, c.first_name, c.last_name, c.phone, c.national_id,
            COUNT(f.id) as flat_count
        FROM customers c
        LEFT JOIN flats f ON c.id = f.owner_id
        GROUP BY c.id
        ORDER BY c.first_name, c.last_name
    """)
    customers = cur.fetchall()
    
    cur.close()
    conn.close()

    return render_template('customers.html', 
                           customers=customers, 
                           user_name=session.get('user_name')) 


@app.route('/checks')
def list_checks():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    
    # --- YENİ: Daha detaylı özet için değişkenler ---
    total_incoming_portfolio = Decimal(0)
    total_outgoing_portfolio = Decimal(0)
    
    # Hata durumunda boş dönmeleri için burada tanımlıyoruz
    incoming_checks, outgoing_checks = [], []

    try:
        # Alınan Çekleri Çek (Müşterilerden)
        cur.execute("""
            SELECT 
                c.id, c.due_date, c.amount, cus.first_name || ' ' || cus.last_name AS customer_name,
                c.bank_name, c.check_number, c.status
            FROM checks c
            LEFT JOIN customers cus ON c.customer_id = cus.id
            ORDER BY c.due_date ASC
        """)
        incoming_checks = cur.fetchall()
        # Sadece 'portfoyde' olanların toplamını hesapla
        for check in incoming_checks:
            if check[6] == 'portfoyde':
                total_incoming_portfolio += check[2]

        # Verilen Çekleri Çek (Tedarikçilere)
        cur.execute("""
            SELECT 
                oc.id, oc.due_date, oc.amount, s.name AS supplier_name,
                oc.bank_name, oc.check_number, oc.status
            FROM outgoing_checks oc
            LEFT JOIN suppliers s ON oc.supplier_id = s.id
            ORDER BY oc.due_date ASC
        """)
        outgoing_checks = cur.fetchall()
        # Sadece 'verildi' durumunda olanların toplamını hesapla
        for check in outgoing_checks:
            if check[6] == 'verildi':
                total_outgoing_portfolio += check[2]

    except Exception as e:
        flash(f"Çekler listelenirken bir hata oluştu: {e}", "danger")
    finally:
        cur.close()
        conn.close()

    # Net çek pozisyonunu hesapla
    net_check_position = total_incoming_portfolio - total_outgoing_portfolio

    return render_template('checks.html', 
                           incoming_checks=incoming_checks,
                           outgoing_checks=outgoing_checks,
                           user_name=session.get('user_name'),
                           today=date.today(),
                           # --- YENİ: Hesaplanan toplamları şablona gönder ---
                           total_incoming_portfolio=total_incoming_portfolio,
                           total_outgoing_portfolio=total_outgoing_portfolio,
                           net_check_position=net_check_position
                           )


# update_check_status fonksiyonu
@app.route('/check/update_status', methods=['POST'])
def update_check_status():
    """Bir çeki 'tahsil edildi', 'ödendi' veya 'karşılıksız' olarak işaretler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    check_id = request.form.get('check_id')
    check_type = request.form.get('check_type') 
    new_status = request.form.get('new_status')

    conn = get_connection()
    cur = conn.cursor()

    try:
        if check_type == 'incoming':
            cur.execute("SELECT amount FROM checks WHERE id = %s", (check_id,))
            check_details = cur.fetchone()
            if not check_details: raise ValueError("Güncellenecek müşteri çeki bulunamadı.")
            payment_amount = check_details[0]

            cur.execute("UPDATE checks SET status = %s WHERE id = %s", (new_status, check_id))

            if new_status == 'tahsil_edildi':
                cur.execute("SELECT flat_id FROM payments WHERE check_id = %s", (check_id,))
                payment_record = cur.fetchone()
                if not payment_record: raise ValueError("Bu çeke bağlı bir ödeme kaydı bulunamadı.")
                flat_id = payment_record[0]

                amount_to_distribute = payment_amount
                cur.execute("SELECT id, amount, paid_amount FROM installment_schedule WHERE flat_id = %s AND is_paid = FALSE ORDER BY due_date ASC", (flat_id,))
                unpaid_installments = cur.fetchall()
                for inst_id, total_amount, paid_amount in unpaid_installments:
                    if amount_to_distribute <= 0: break
                    remaining_due = total_amount - (paid_amount or 0)
                    if amount_to_distribute >= remaining_due:
                        cur.execute("UPDATE installment_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, inst_id))
                        amount_to_distribute -= remaining_due
                    else:
                        new_paid_amount = (paid_amount or 0) + amount_to_distribute
                        cur.execute("UPDATE installment_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, inst_id))
                        amount_to_distribute = 0
                flash(f'{format_thousands(payment_amount)} ₺ tutarındaki çek tahsil edildi ve borca yansıtıldı.', 'success')

        elif check_type == 'outgoing':
            cur.execute("SELECT amount, supplier_id FROM outgoing_checks WHERE id = %s", (check_id,))
            check_details = cur.fetchone()
            if not check_details: raise ValueError("Güncellenecek firma çeki bulunamadı.")
            payment_amount, supplier_id = check_details

            # 1. Çekin durumunu veritabanında güncelle
            cur.execute("UPDATE outgoing_checks SET status = %s WHERE id = %s", (new_status, check_id))

            # 2. Eğer çek "odendi" olarak işaretlendiyse, tutarını tedarikçinin borcundan düş
            if new_status == 'odendi':
                amount_to_distribute = payment_amount
                # Tedarikçinin ödenmemiş tüm taksitlerini en eskiden başlayarak bul
                cur.execute("""
                    SELECT es.id, es.amount, es.paid_amount 
                    FROM expense_schedule es
                    JOIN expenses e ON es.expense_id = e.id
                    WHERE e.supplier_id = %s AND es.is_paid = FALSE
                    ORDER BY es.due_date ASC
                """, (supplier_id,))
                unpaid_installments = cur.fetchall()

                for inst_id, total_amount, paid_amount in unpaid_installments:
                    if amount_to_distribute <= 0: break
                    remaining_due = total_amount - (paid_amount or 0)
                    if amount_to_distribute >= remaining_due:
                        cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, inst_id))
                        amount_to_distribute -= remaining_due
                    else:
                        new_paid_amount = (paid_amount or 0) + amount_to_distribute
                        cur.execute("UPDATE expense_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, inst_id))
                        amount_to_distribute = 0
                flash(f'{format_thousands(payment_amount)} ₺ tutarındaki çek ödendi ve gider borcuna yansıtıldı.', 'success')
            else:
                 flash('Verilen çekin durumu başarıyla güncellendi.', 'success')

        conn.commit()

    except Exception as e:
        conn.rollback()
        flash(f"Çek durumu güncellenirken bir hata oluştu: {e}", "danger")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('list_checks'))



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

# app.py içindeki cooperative_report fonksiyonunu bulun ve güncelleyin

@app.route('/reports/cooperative/<int:project_id>/<int:year>/<int:month>')
def cooperative_report(project_id, year, month):
    """Belirli bir kooperatif projesinin aylık finansal raporunu gösterir."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    report_data = {}
    today = date.today() # Gider durumu için

    turkish_months = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
        7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
    }

    try:
        start_date = date(year, month, 1)
        end_date = (start_date + relativedelta(months=1)) - relativedelta(days=1)

        cur.execute("SELECT name, total_flats FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()
        report_data['project_name'] = project_info[0]
        
        cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s AND owner_id IS NOT NULL", (project_id,))
        member_count = cur.fetchone()[0]
        report_data['member_count'] = member_count

        # 1. Önceki Aydan Devreden Bakiye (Gerçekleşenlere göre)
        cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id LEFT JOIN checks c ON p.check_id = c.id WHERE f.project_id = %s AND p.payment_date < %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')", (project_id, start_date))
        total_income_before = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(sp.amount), 0) FROM supplier_payments sp JOIN expenses e ON sp.expense_id = e.id LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id WHERE e.project_id = %s AND sp.payment_date < %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')", (project_id, start_date))
        total_large_expense_before = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date < %s", (project_id, start_date))
        total_petty_cash_before = cur.fetchone()[0]
        total_expense_before = total_large_expense_before + total_petty_cash_before
        previous_balance = total_income_before - total_expense_before
        report_data['previous_balance'] = previous_balance

        # 2. Bu Ayın Gerçekleşen Gelir ve Giderleri
        cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id LEFT JOIN checks c ON p.check_id = c.id WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')", (project_id, start_date, end_date))
        current_income = cur.fetchone()[0]
        report_data['current_income'] = current_income
        cur.execute("SELECT COALESCE(SUM(sp.amount), 0) FROM supplier_payments sp JOIN expenses e ON sp.expense_id = e.id LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id WHERE e.project_id = %s AND sp.payment_date BETWEEN %s AND %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')", (project_id, start_date, end_date))
        current_large_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s AND expense_date BETWEEN %s AND %s", (project_id, start_date, end_date))
        current_petty_cash_expense = cur.fetchone()[0]
        current_expense = current_large_expense + current_petty_cash_expense
        report_data['current_expense'] = current_expense
        
        # 3. Ay Sonu Bakiyesi
        end_of_month_balance = previous_balance + current_income - current_expense
        report_data['end_of_month_balance'] = end_of_month_balance

        # --- 4. Detaylı Listeler ---

        # Gelir Detayları (Geliştirilmiş Sorgu)
        cur.execute("""
            SELECT 
                p.payment_date, 
                c.first_name || ' ' || c.last_name AS customer_name,
                f.block_name, f.floor, f.flat_no,
                p.description, p.amount, p.payment_method,
                chk.status AS check_status
            FROM payments p 
            JOIN flats f ON p.flat_id = f.id 
            JOIN customers c ON f.owner_id = c.id
            LEFT JOIN checks chk ON p.check_id = chk.id
            WHERE f.project_id = %s AND p.payment_date BETWEEN %s AND %s 
            ORDER BY p.payment_date, p.id
        """, (project_id, start_date, end_date))
        income_details_raw = cur.fetchall()
        income_details = []
        for row in income_details_raw:
            # Durumu ve CSS sınıfını belirle
            method = row[7]
            check_status = row[8]
            status_text = ""
            status_class = ""
            if method == 'çek':
                if check_status == 'portfoyde': status_text, status_class = "Portföyde", "warning"
                elif check_status == 'tahsil_edildi': status_text, status_class = "Tahsil Edildi", "success"
                elif check_status == 'karsiliksiz': status_text, status_class = "Karşılıksız", "danger"
                else: status_text, status_class = "Bilinmiyor", "secondary"
            else: # Nakit
                 status_text, status_class = "Ödendi", "success"
                 
            income_details.append({
                'date': row[0],
                'customer': row[1],
                'flat': f"Blok: {row[2] or 'N/A'}, K:{row[3]}, N:{row[4]}",
                'description': row[5],
                'amount': row[6],
                'method': method,
                'status_text': status_text,
                'status_class': status_class
            })
        report_data['income_details'] = income_details

        # Gider Detayları (Geliştirilmiş Sorgular)
        expense_details = []
        # Büyük Giderler (Gerçekleşen Ödemeler)
        cur.execute("""
            SELECT 
                sp.payment_date, e.title, s.name AS supplier_name, sp.description,
                sp.amount, sp.payment_method, oc.status AS check_status
            FROM supplier_payments sp
            JOIN expenses e ON sp.expense_id = e.id
            LEFT JOIN suppliers s ON e.supplier_id = s.id
            LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
            WHERE e.project_id = %s AND sp.payment_date BETWEEN %s AND %s
            ORDER BY sp.payment_date, sp.id
        """, (project_id, start_date, end_date))
        large_expense_payments = cur.fetchall()
        for row in large_expense_payments:
            method = row[5]
            check_status = row[6]
            status_text = ""
            status_class = ""
            if method == 'çek':
                if check_status == 'verildi': status_text, status_class = "Verildi", "warning"
                elif check_status == 'odendi': status_text, status_class = "Ödendi", "success"
                elif check_status == 'karsiliksiz': status_text, status_class = "Karşılıksız", "danger"
                else: status_text, status_class = "Bilinmiyor", "secondary"
            else: # Nakit
                status_text, status_class = "Ödendi", "success"
                
            expense_details.append({
                'date': row[0],
                'type': 'Büyük Gider',
                'title': row[1],
                'supplier': row[2] or '-',
                'description': row[3],
                'amount': row[4],
                'method': method,
                'status_text': status_text,
                'status_class': status_class
            })

        # Küçük Nakit Giderler
        cur.execute("""
            SELECT expense_date, title, description, amount 
            FROM petty_cash_expenses 
            WHERE project_id = %s AND expense_date BETWEEN %s AND %s
            ORDER BY expense_date, id
        """, (project_id, start_date, end_date))
        petty_cash_details_raw = cur.fetchall()
        for row in petty_cash_details_raw:
            expense_details.append({
                'date': row[0],
                'type': 'Küçük Gider',
                'title': row[1],
                'supplier': 'Kasa', # Küçük giderler kasadan çıkar
                'description': row[2],
                'amount': row[3],
                'method': 'nakit',
                'status_text': 'Ödendi',
                'status_class': 'success'
            })
        
        # Tüm giderleri tarihe göre sırala
        expense_details.sort(key=lambda x: x['date']) 
        report_data['expense_details'] = expense_details
        
        month_name = turkish_months.get(start_date.month, "")
        report_data['report_period'] = f"{month_name} {start_date.year}"

    except Exception as e:
        flash(f"Rapor oluşturulurken bir hata oluştu: {e}", "danger")
        # Hata durumunda boş listeler ata
        report_data['income_details'] = []
        report_data['expense_details'] = []
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
    income_data, expense_data = [], []
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
            income_data = cur.fetchall()

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
            expense_data = cur.fetchall()

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
        net_cash_flow=net_cash_flow
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

    income_items = []
    expense_items = []
    today = date.today()
    project_name = "Bilinmiyor"
    project_type = "normal"

    total_paid_income = Decimal(0)
    total_unpaid_income = Decimal(0)
    total_paid_expense = Decimal(0)
    total_unpaid_expense = Decimal(0)

    try:
        cur.execute("SELECT name, project_type FROM projects WHERE id = %s", (project_id,))
        project_info = cur.fetchone()
        project_name, project_type = project_info

        # --- ÖZET KARTI HESAPLAMALARI (Doğru haliyle) ---
        if project_type == 'normal':
            cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id LEFT JOIN checks c ON p.check_id = c.id WHERE f.project_id = %s AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')", (project_id,))
            total_paid_income = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(s.amount), 0) FROM installment_schedule s JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s", (project_id,))
            total_planned_income = cur.fetchone()[0]
            total_unpaid_income = total_planned_income - total_paid_income
        elif project_type == 'cooperative':
            cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s", (project_id,))
            total_paid_income = cur.fetchone()[0]
            total_unpaid_income = 0
        
        cur.execute("SELECT COALESCE(SUM(sp.amount), 0) FROM supplier_payments sp JOIN expenses e ON sp.expense_id = e.id LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id WHERE e.project_id = %s AND (sp.payment_method = 'nakit' OR oc.status = 'odendi')", (project_id,))
        paid_large_expenses = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        paid_petty_cash = cur.fetchone()[0]
        total_paid_expense = paid_large_expenses + paid_petty_cash
        cur.execute("SELECT COALESCE(SUM(es.amount), 0) FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id WHERE e.project_id = %s", (project_id,))
        total_planned_large_expense = cur.fetchone()[0]
        # Küçük giderler planlanmadığı için planlanan gidere eklenmez ama ödenmişe eklenir. Kalan borç hesabı için düzeltme:
        total_planned_expenses_for_summary = total_planned_large_expense + paid_petty_cash # Özet kart için toplam plan + gerçekleşen küçükler
        total_unpaid_expense = total_planned_expenses_for_summary - total_paid_expense # Özet kart için kalan borç


        # --- GELİR TABLOSU VERİLERİ ---
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
                elif paid_this_installment > 0: status, status_class, payment_method = "Kısmen Ödendi", "bg-info text-dark", None
                else: status, status_class, payment_method = ("Gecikmiş", "bg-danger") if due_date < today else ("Bekleniyor", "bg-secondary"), None
                income_items.append({'date': due_date, 'description': "Daire Satış Taksiti", 'party': f"{first} {last}", 'details': f"Blok: {block or 'N/A'}, Kat: {floor}, No: {flat_no}", 'amount': amount, 'status': status, 'status_class': status_class, 'payment_method': payment_method})
        elif project_type == 'cooperative':
            coop_income_query = """
                SELECT p.payment_date, p.amount, p.description, p.payment_method, c.first_name, c.last_name, f.block_name, f.floor, f.flat_no, chk.status AS check_status
                FROM payments p JOIN flats f ON p.flat_id = f.id JOIN customers c ON f.owner_id = c.id LEFT JOIN checks chk ON p.check_id = chk.id
                WHERE f.project_id = %s ORDER BY p.payment_date DESC
            """
            cur.execute(coop_income_query, (project_id,))
            for row in cur.fetchall():
                payment_date, amount, description, payment_method, first, last, block, floor, flat_no, check_status = row
                if payment_method == 'çek':
                    if check_status == 'portfoyde': status, status_class = "Çek Portföyde", "bg-warning text-dark"
                    elif check_status == 'tahsil_edildi': status, status_class = "Tahsil Edildi", "bg-success"
                    elif check_status == 'karsiliksiz': status, status_class = "Karşılıksız Çek", "bg-danger"
                    else: status, status_class = "Bilinmiyor", "bg-secondary"
                else: status, status_class = "Ödendi", "bg-success"
                income_items.append({'date': payment_date, 'description': description or "Üye Katkı Payı", 'party': f"{first} {last}", 'details': f"Blok: {block or 'N/A'}, Kat: {floor}, No: {flat_no}", 'amount': amount, 'status': status, 'status_class': status_class, 'payment_method': payment_method})

        # --- GİDER TABLOSU VERİLERİ (İSTENEN ÖNCEKİ HALİNE DÖNDÜRÜLDÜ) ---
        # Tüm planlanmış gider taksitlerini ve son ödeme bilgisini çek
        expense_query = """
             SELECT 
                 s.due_date, s.amount, s.paid_amount, s.is_paid, 
                 e.title, sup.name,
                 p.payment_method, -- Son ödemenin yöntemi
                 oc.status as check_status -- Son ödeme çek ise durumu
             FROM expense_schedule s
             JOIN expenses e ON s.expense_id = e.id
             LEFT JOIN suppliers sup ON e.supplier_id = sup.id
             -- Son ödemeyi bulmak için LATERAL JOIN
             LEFT JOIN LATERAL (
                 SELECT * FROM supplier_payments 
                 WHERE supplier_payments.expense_id = s.expense_id 
                 ORDER BY supplier_payments.payment_date DESC, supplier_payments.id DESC 
                 LIMIT 1
             ) p ON TRUE
             LEFT JOIN outgoing_checks oc ON p.check_id = oc.id
             WHERE e.project_id = %s
         """
        cur.execute(expense_query, (project_id,))
        for row in cur.fetchall():
            due_date, amount, paid_amount, is_paid, title, sup_name, last_payment_method, check_status = row
            paid_amount = paid_amount or Decimal(0)

            # Durumu belirle
            if is_paid:
                status, status_class = "Ödendi", "bg-success"
            elif paid_amount > 0:
                status, status_class = "Kısmen Ödendi", "bg-info text-dark"
            elif due_date < today:
                status, status_class = "Gecikmiş", "bg-danger"
            else:
                status, status_class = "Bekleniyor", "bg-secondary"

            # Ödeme yöntemini belirle (Sadece tamamen veya kısmen ödenmişse ve son ödeme varsa göster)
            display_method = None
            if is_paid or paid_amount > 0:
                 # Eğer son ödeme çek ise ve hala portföydeyse durumu özel belirt
                 if last_payment_method == 'çek' and check_status == 'verildi':
                      status, status_class = "Çek Verildi", "bg-warning text-dark" # Ödendi yerine bunu göster
                      display_method = 'çek' # Yöntem yine de çek
                 else:
                     display_method = last_payment_method # Son ödeme nakitse veya çek ödenmişse

            expense_items.append({
                'date': due_date, 
                'description': title, 
                'party': sup_name or "Belirtilmemiş",
                'details': 'Planlı Gider', 
                'amount': amount, 
                'status': status,
                'status_class': status_class, 
                'payment_method': display_method # Hesaplanan yöntemi kullan
            })

        # Küçük Nakit Giderleri Ekle
        cur.execute("SELECT expense_date, title, amount, description FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        for expense_date, title, amount, desc in cur.fetchall():
            expense_items.append({'date': expense_date, 'description': title, 'party': 'Kasa', 'details': desc or 'Küçük Gider', 'amount': amount, 'status': 'Ödendi', 'status_class': 'bg-success', 'payment_method': 'nakit'})

        # Filtreleme
        if start_date_str:
            start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] >= start_date_obj]
            expense_items = [e for e in expense_items if e['date'] >= start_date_obj]
        if end_date_str:
            end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            income_items = [i for i in income_items if i['date'] <= end_date_obj]
            expense_items = [e for e in expense_items if e['date'] <= end_date_obj]

        # Sıralama
        is_reverse = (order == 'desc')
        key_to_sort = 'party' if sort_by == 'party' else 'date'
        # Gelir ve Gider listelerini ayrı ayrı sırala
        income_items.sort(key=lambda x: x.get(key_to_sort, today if key_to_sort == 'date' else ''), reverse=is_reverse)
        expense_items.sort(key=lambda x: x.get('date', today), reverse=is_reverse) # Giderleri her zaman tarihe göre sırala (şimdilik)


    except Exception as e:
        flash(f"Proje genel bakışı oluşturulurken hata: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    
    return render_template('project_overview.html', project_id=project_id, project_name=project_name, income_items=income_items, expense_items=expense_items, total_paid_income=total_paid_income, total_unpaid_income=total_unpaid_income, total_paid_expense=total_paid_expense, total_unpaid_expense=total_unpaid_expense, user_name=session.get('user_name'), project_type=project_type, start_date=start_date_str, end_date=end_date_str, sort_by=sort_by, order=order)

# list_expenses fonksiyonu

# @app.route('/project/<int:project_id>/expenses')
# def list_expenses(project_id):
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     conn = get_connection()
#     cur = conn.cursor()
#     expenses_data = []
#     petty_cash_items = []
#     project_name = ""
#     total_project_expense = Decimal(0)

#     try:
#         cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
#         project_name_result = cur.fetchone()
#         if not project_name_result:
#             flash("Proje bulunamadı.", "danger")
#             return redirect(url_for('dashboard'))
#         project_name = project_name_result[0]

#         # 1. Planlı/Büyük Giderleri Çek
#         cur.execute("""
#             SELECT e.id, e.title, e.amount, e.description, s.name as supplier_name
#             FROM expenses e
#             LEFT JOIN suppliers s ON e.supplier_id = s.id
#             WHERE e.project_id = %s
#             ORDER BY e.id DESC
#         """, (project_id,))
#         expenses_raw = cur.fetchall()

#         # 2. Planlı Giderlerin Taksitlerini Çek
#         cur.execute("""
#             SELECT expense_id, due_date, sch.amount, is_paid, paid_amount, sch.id as installment_id
#             FROM expense_schedule sch
#             JOIN expenses e ON sch.expense_id = e.id
#             WHERE e.project_id = %s
#             ORDER BY expense_id, sch.due_date ASC
#         """, (project_id,))
#         schedules_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

#         # 3. Küçük Giderleri Çek
#         cur.execute("""
#             SELECT id, title, amount, expense_date, description
#             FROM petty_cash_expenses
#             WHERE project_id = %s
#             ORDER BY expense_date DESC, id DESC
#         """, (project_id,))
#         petty_cash_items = cur.fetchall()

#         # 4. Projenin Toplam Giderini Doğru Hesapla
#         cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
#         total_planned_expense = cur.fetchone()[0]
#         cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
#         total_petty_cash = cur.fetchone()[0]
#         total_project_expense = total_planned_expense + total_petty_cash

#         # 5. Projenin ödenen giderlerini hesapla
#         # Planlı (büyük) giderler için expense_schedule tablosundaki paid_amount toplamını al
#         cur.execute("""
#             SELECT COALESCE(SUM(sch.paid_amount), 0)
#             FROM expense_schedule sch
#             JOIN expenses e ON sch.expense_id = e.id
#             WHERE e.project_id = %s
#         """, (project_id,))
#         total_paid_scheduled = cur.fetchone()[0] or Decimal(0)

#         # Küçük giderler (petty cash) kayıt edildiği anda ödendi sayıldığı için onların toplamı
#         # zaten total_petty_cash değişkeninde yer alıyor.
#         total_paid_project = (total_paid_scheduled or Decimal(0)) + (total_petty_cash or Decimal(0))

#         total_remaining_due = total_project_expense - total_paid_project


#         # 5. Planlı gider verilerini işle
#         today = date.today()
#         for expense_id, title, total_amount, description, supplier_name in expenses_raw:
#             schedule = schedules_by_expense.get(expense_id, [])
#             total_paid_for_this_expense = sum(item[4] for item in schedule if item[4] is not None)
#             expense_dict = {
#                 'expense_id': expense_id, 'title': title, 'supplier_name': supplier_name or "Tedarikçi Belirtilmemiş",
#                 'total_amount': total_amount, 'total_paid': total_paid_for_this_expense,
#                 'remaining_due': total_amount - total_paid_for_this_expense, 'installments': []
#             }
#             for _, due_date, inst_amount, is_paid, paid_amount, inst_id in schedule:
#                 paid_amount = paid_amount or Decimal(0)
#                 status, css_class = ("Ödendi", "table-success") if is_paid else (f"Kısmen Ödendi", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
#                 expense_dict['installments'].append({
#                     'id': inst_id, 'due_date': due_date, 'total_amount': inst_amount,
#                     'remaining_installment_due': inst_amount - paid_amount,
#                     'status': status, 'css_class': css_class, 'is_paid': is_paid
#                 })
#             expenses_data.append(expense_dict)
            
#     except Exception as e:
#         flash(f"Giderler listelenirken bir hata oluştu: {e}", "danger")
#         print(f"EXPENSES PAGE ERROR: {e}")
#     finally:
#         cur.close()
#         conn.close()

#     return render_template('expenses.html',
#                            project_id=project_id,
#                            project_name=project_name,
#                            expenses_data=expenses_data,
#                            petty_cash_items=petty_cash_items,
#                            total_project_expense=total_project_expense,
#                            total_paid_project=total_paid_project,
#                             total_remaining_due=total_remaining_due,
#                            user_name=session.get('user_name'))



@app.route('/project/<int:project_id>/petty_cash/add', methods=['POST'])
def add_petty_cash(project_id):
    """Bir projeye yeni bir küçük gider ekler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        title = request.form.get('petty_cash_title')
        amount = Decimal(request.form.get('petty_cash_amount'))
        expense_date = request.form.get('petty_cash_date')
        description = request.form.get('petty_cash_description')

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

    return redirect(url_for('list_expenses', project_id=project_id))


@app.route('/petty_cash/<int:item_id>/delete', methods=['POST'])
def delete_petty_cash(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    project_id = request.form.get('project_id')
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
        return redirect(url_for('list_expenses', project_id=project_id))
    return redirect(url_for('dashboard'))


@app.route('/petty_cash/<int:item_id>/edit', methods=['GET', 'POST'])
def edit_petty_cash(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title')
        amount = Decimal(request.form.get('amount'))
        expense_date = request.form.get('expense_date')
        description = request.form.get('description')
        project_id = request.args.get('project_id') or request.form.get('project_id')
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
            return redirect(url_for('list_expenses', project_id=project_id))
        return redirect(url_for('dashboard'))

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
    return render_template('edit_petty_cash.html', item=item, project_id=row[5])


@app.route('/expense/<int:expense_id>/manage_plan', methods=['GET', 'POST'])
def manage_expense_plan(expense_id):
    """Bir giderin ödeme planını (taksitlerini) yönetir."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            due_dates = request.form.getlist('due_date[]')
            amounts = request.form.getlist('amount[]')

            cur.execute("DELETE FROM expense_schedule WHERE expense_id = %s AND is_paid = FALSE", (expense_id,))
            
            for date_str, amount_str in zip(due_dates, amounts):
                if date_str and amount_str:
                    due_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    amount = Decimal(amount_str)
                    cur.execute("SELECT id FROM expense_schedule WHERE expense_id = %s AND due_date = %s AND amount = %s AND is_paid = TRUE", (expense_id, due_date, amount))
                    if cur.fetchone() is None:
                        cur.execute("INSERT INTO expense_schedule (expense_id, due_date, amount) VALUES (%s, %s, %s)", (expense_id, due_date, amount))
            
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expense_schedule WHERE expense_id = %s", (expense_id,))
            total_amount = cur.fetchone()[0]
            cur.execute("UPDATE expenses SET amount = %s WHERE id = %s", (total_amount, expense_id))

            conn.commit()
            flash('Gider planı başarıyla güncellendi!', 'success')
            
            cur.execute("SELECT project_id FROM expenses WHERE id = %s", (expense_id,))
            project_id = cur.fetchone()[0]
            return redirect(url_for('list_expenses', project_id=project_id))

        except Exception as e:
            conn.rollback()
            flash(f'Gider planı güncellenirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('manage_expense_plan', expense_id=expense_id))
        finally:
            cur.close()
            conn.close()

    try:
        cur.execute("SELECT e.title, p.name, e.project_id FROM expenses e JOIN projects p ON e.project_id = p.id WHERE e.id = %s", (expense_id,))
        expense_info = cur.fetchone()

        if not expense_info:
            flash('Planı yönetilecek gider bulunamadı.', 'danger')
            return redirect(url_for('dashboard'))

        cur.execute("SELECT due_date, amount, is_paid FROM expense_schedule WHERE expense_id = %s ORDER BY due_date", (expense_id,))
        existing_installments = cur.fetchall()
        
    finally:
        cur.close()
        conn.close()

    return render_template('manage_expense_plan.html', 
                           expense_id=expense_id,
                           expense_info=expense_info,
                           existing_installments=existing_installments,
                           user_name=session.get('user_name'))


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


@app.route('/flat/<int:flat_id>/manage_plan', methods=['GET', 'POST'])
def manage_payment_plan(flat_id):
    """
    Bir dairenin ödeme planını yönetir (görüntüler, günceller, ekler, siler).
    POST isteğinde, projenin tüm taksitlerini siler ve formdan gelen güncel listeyi kaydeder.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            due_dates = request.form.getlist('due_date[]')
            amounts = request.form.getlist('amount[]')

            # 1. Bu daireye ait, henüz ödenmemiş taksitleri sil. Ödenmiş olanlara dokunma.
            cur.execute("DELETE FROM installment_schedule WHERE flat_id = %s AND is_paid = FALSE", (flat_id,))
            
            # 2. Formdan gelen güncel listeyi veritabanına ekle
            for date_str, amount_str in zip(due_dates, amounts):
                if date_str and amount_str:
                    due_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    amount = Decimal(amount_str)
                    # Ödenmiş bir taksit zaten var mı diye kontrol et
                    cur.execute("SELECT id FROM installment_schedule WHERE flat_id = %s AND due_date = %s AND amount = %s AND is_paid = TRUE", (flat_id, due_date, amount))
                    if cur.fetchone() is None:
                        cur.execute("""
                            INSERT INTO installment_schedule (flat_id, due_date, amount, is_paid, paid_amount)
                            VALUES (%s, %s, %s, FALSE, 0)
                        """, (flat_id, due_date, amount))
            
            # 3. Dairenin toplam fiyat ve taksit sayısını yeniden hesapla ve güncelle
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM installment_schedule WHERE flat_id = %s", (flat_id,))
            total_price = cur.fetchone()[0]
            cur.execute("SELECT COUNT(id) FROM installment_schedule WHERE flat_id = %s", (flat_id,))
            total_installments = cur.fetchone()[0]
            cur.execute("UPDATE flats SET total_price = %s, total_installments = %s WHERE id = %s",
                        (total_price, total_installments, flat_id))

            conn.commit()
            flash('Ödeme planı başarıyla güncellendi!', 'success')
            return redirect(url_for('debt_status'))

        except Exception as e:
            conn.rollback()
            flash(f'Plan güncellenirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('manage_payment_plan', flat_id=flat_id))
        finally:
            cur.close()
            conn.close()

    try:
        cur.execute("""
            SELECT p.name, f.block_name, f.floor, f.flat_no 
            FROM flats f JOIN projects p ON f.project_id = p.id WHERE f.id = %s
        """, (flat_id,))
        flat_info = cur.fetchone()

        if not flat_info:
            flash('Ödeme planı yönetilecek daire bulunamadı.', 'danger')
            return redirect(url_for('dashboard'))

        cur.execute("SELECT due_date, amount, is_paid FROM installment_schedule WHERE flat_id = %s ORDER BY due_date", (flat_id,))
        existing_installments = cur.fetchall()
        
    finally:
        cur.close()
        conn.close()

    return render_template('manage_payment_plan.html', 
                           flat_id=flat_id,
                           flat_info=flat_info,
                           existing_installments=existing_installments,
                           user_name=session.get('user_name'))


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

        # 4. Verileri işleyip şablon için hazırla
        statement_data = {
            'project_name': statement_info_raw[0],
            'customer_name': f"{statement_info_raw[1]} {statement_info_raw[2]}",
            'flat_details': f"Blok: {statement_info_raw[6] or 'N/A'}, Kat: {statement_info_raw[3]}, No: {statement_info_raw[4]}",
            'flat_total_price': statement_info_raw[5] or 0,
            'total_paid': total_paid,
            'remaining_debt': (statement_info_raw[5] or 0) - total_paid,
            'print_date': date.today(),
            'installments': []
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

        return render_template('print_statement.html', data=statement_data)

    except Exception as e:
        flash(f"Döküm oluşturulurken bir hata oluştu: {e}", "danger")
        return redirect(url_for('debt_status'))
    finally:
        cur.close()
        conn.close()


# @app.route('/expenses/all')
# def all_expenses():
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     conn = get_connection()
#     cur = conn.cursor()

#     # Filtre parametreleri
#     selected_project = request.args.get('project')
#     start_date = request.args.get('start_date')
#     end_date = request.args.get('end_date')
#     sort_by = request.args.get('sort_by', 'tarih')
#     order = request.args.get('order', 'desc')

#     # Dinamik sıralama
#     sort_column = {
#         'tarih': 'expense_date',
#         'proje': 'project_name',
#         'tutar': 'amount'
#     }.get(sort_by, 'expense_date')

#     order_sql = 'ASC' if order == 'asc' else 'DESC'

#     try:
#         # Tüm projeler (filtre dropdown için)
#         cur.execute("SELECT DISTINCT name FROM projects ORDER BY name")
#         all_projects = [r[0] for r in cur.fetchall()]

#         query = """
#             SELECT 
#                 p.name AS project_name,
#                 e.title,
#                 e.amount,
#                 e.expense_date,
#                 e.description,
#                 s.name AS supplier_name,
#                 'Büyük Gider' AS expense_type
#             FROM expenses e
#             LEFT JOIN projects p ON e.project_id = p.id
#             LEFT JOIN suppliers s ON e.supplier_id = s.id
#             UNION ALL
#             SELECT 
#                 p.name AS project_name,
#                 pe.title,
#                 pe.amount,
#                 pe.expense_date,
#                 pe.description,
#                 NULL AS supplier_name,
#                 'Küçük Gider' AS expense_type
#             FROM petty_cash_expenses pe
#             LEFT JOIN projects p ON pe.project_id = p.id
#         """

#         # Filtreleme
#         filters = []
#         params = []
#         if selected_project:
#             filters.append("project_name = %s")
#             params.append(selected_project)
#         if start_date:
#             filters.append("expense_date >= %s")
#             params.append(start_date)
#         if end_date:
#             filters.append("expense_date <= %s")
#             params.append(end_date)
#         if filters:
#             query = f"SELECT * FROM ({query}) t WHERE {' AND '.join(filters)}"

#         query += f" ORDER BY {sort_column} {order_sql}"

#         cur.execute(query, params)
#         expenses = cur.fetchall()

#     except Exception as e:
#         flash(f"Giderler listelenirken hata oluştu: {e}", "danger")
#         expenses, all_projects = [], []
#     finally:
#         cur.close()
#         conn.close()

#     return render_template(
#         'all_expenses.html',
#         expenses=expenses,
#         all_projects=all_projects,
#         selected_project=selected_project,
#         start_date=start_date,
#         end_date=end_date,
#         sort_by=sort_by,
#         order=order,
#         user_name=session.get('user_name')
#     )



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

    try:
        cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
        projects = cur.fetchall()
        project_summaries = []

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

                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    WHERE f.project_id = %s
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

            elif project_type == 'cooperative':
                # --- Kooperatif gelirleri (aidatlar) ---
                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM payments p
                    JOIN flats f ON p.flat_id = f.id
                    WHERE f.project_id = %s
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
        flash(f"Raporlar oluşturulurken hata oluştu: {e}", "danger")
        project_summaries = []
    finally:
        cur.close()
        conn.close()

    return render_template('reports.html',
                           project_summaries=project_summaries,
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

# new_payment fonksiyonu

# @app.route('/payment/new', methods=['GET', 'POST'])
# def new_payment():
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     if request.method == 'POST':
#         conn = None
#         try:
#             conn = get_connection()
#             cur = conn.cursor()

#             flat_id = int(request.form.get('flat_id'))
#             payment_amount = Decimal(request.form.get('amount'))
#             payment_date_str = request.form.get('payment_date') 
#             description = request.form.get('description', '')
#             payment_method = request.form.get('payment_method', 'nakit')

#             if not all([flat_id, payment_amount, payment_date_str]):
#                 flash('Lütfen tüm zorunlu alanları doldurun.', 'danger')
#                 return redirect(url_for('new_payment'))

#             payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()

#             cur.execute("SELECT owner_id FROM flats WHERE id = %s", (flat_id,))
#             customer_id_result = cur.fetchone()
#             if not customer_id_result:
#                 flash('Daire sahibi bulunamadı.', 'danger')
#                 return redirect(url_for('new_payment'))
#             customer_id = customer_id_result[0]

#             if payment_method == 'nakit':
#                 cur.execute(
#                     "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method) VALUES (%s, %s, %s, %s, %s)",
#                     (flat_id, payment_amount, payment_date, description or 'Nakit Ödeme', 'nakit')
#                 )
                
#                 amount_to_distribute = payment_amount
#                 cur.execute("SELECT id, amount, paid_amount FROM installment_schedule WHERE flat_id = %s AND is_paid = FALSE ORDER BY due_date ASC", (flat_id,))
#                 unpaid_installments = cur.fetchall()
#                 for inst_id, total_amount, paid_amount in unpaid_installments:
#                     if amount_to_distribute <= 0: break
#                     remaining_due = total_amount - paid_amount
#                     if amount_to_distribute >= remaining_due:
#                         cur.execute("UPDATE installment_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, inst_id))
#                         amount_to_distribute -= remaining_due
#                     else:
#                         new_paid_amount = paid_amount + amount_to_distribute
#                         cur.execute("UPDATE installment_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, inst_id))
#                         amount_to_distribute = 0
                
#                 flash(f'{format_thousands(payment_amount)} ₺ tutarındaki nakit ödeme kaydedildi ve borca yansıtıldı.', 'success')

#             elif payment_method == 'çek':
#                 due_date_str = request.form.get('check_due_date')
#                 bank_name = request.form.get('check_bank_name')
#                 check_number = request.form.get('check_number')

#                 if not due_date_str:
#                     flash('Çek ödemesi için Vade Tarihi zorunludur.', 'danger')
#                     return redirect(url_for('new_payment'))

#                 due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()

#                 cur.execute(
#                     "INSERT INTO checks (customer_id, bank_name, check_number, amount, issue_date, due_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
#                     (customer_id, bank_name, check_number, payment_amount, payment_date, due_date)
#                 )
#                 check_id = cur.fetchone()[0]

#                 cur.execute(
#                     "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method, check_id) VALUES (%s, %s, %s, %s, %s, %s)",
#                     (flat_id, payment_amount, payment_date, description or f'{bank_name} - {check_number} Nolu Çek', 'çek', check_id)
#                 )
                
#                 flash(f'Vadesi {due_date.strftime("%d.%m.%Y")} olan {format_thousands(payment_amount)} ₺ tutarındaki çek başarıyla portföye eklendi.', 'success')

#             conn.commit()
#             return redirect(url_for('debt_status'))

#         except Exception as e:
#             if conn: conn.rollback()
#             flash(f'Ödeme kaydedilirken bir hata oluştu: {e}', 'danger')
#             return redirect(url_for('new_payment'))
#         finally:
#             if conn:
#                 cur.close()
#                 conn.close()

#     # GET isteği
#     conn = get_connection()
#     cur = conn.cursor()
#     cur.execute("SELECT id, name FROM projects ORDER BY name")
#     projects = cur.fetchall()
#     cur.close()
#     conn.close()
#     return render_template('new_payment.html', projects=projects, user_name=session.get('user_name'))

# app.py dosyanızda SADECE BU new_payment fonksiyonu kalmalı

# app.py içindeki new_payment fonksiyonunu bulun ve güncelleyin

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
                   flash(f'Çek kaydedildi ve borca yansıtıldı.', 'success')
                else:
                    flash(f'Çek başarıyla portföye eklendi. Tahsil edildiğinde borca yansıtılacaktır.', 'success')


            conn.commit()
            return redirect(url_for('debt_status'))
        except Exception as e:
            conn.rollback()
            flash(f'Ödeme kaydedilirken bir hata oluştu: {e}', 'danger')
            # Hata durumunda hangi sayfaya yönlendireceğimizi belirle
            redirect_url = url_for('new_payment', installment_id=installment_id) if installment_id else url_for('new_payment', project_id=request.form.get('project_id'), flat_id=request.form.get('flat_id'))
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
def reconcile_customer_payments(cur, flat_id):
    """
    Bir daireye ait tüm taksitlerin ödenen tutarlarını,
    sadece GEÇERLİ ödemelere (nakit veya durumu 'karşılıksız' olmayan çekler)
    göre baştan hesaplar.
    """
    # 1. Bu daire için yapılan GEÇERLİ ödemelerin toplamını al
    cur.execute("""
        SELECT COALESCE(SUM(p.amount), 0)
        FROM payments p
        LEFT JOIN checks c ON p.check_id = c.id
        WHERE p.flat_id = %s AND (p.payment_method = 'nakit' OR c.status != 'karsiliksiz')
    """, (flat_id,))
    total_valid_paid = cur.fetchone()[0]

    # 2. Bu dairenin tüm taksitlerini sıfırla
    cur.execute("UPDATE installment_schedule SET paid_amount = 0, is_paid = FALSE WHERE flat_id = %s", (flat_id,))
    
    # 3. Geçerli toplam ödemeyi taksitlere baştan dağıt
    amount_to_distribute = total_valid_paid
    cur.execute("SELECT id, amount FROM installment_schedule WHERE flat_id = %s ORDER BY due_date ASC", (flat_id,))
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
        except Exception as e:
            conn.rollback()
            flash(f'Güncelleme sırasında hata oluştu: {e}', 'danger')
        finally:
            cur.close()
            conn.close()
        # debt_status sayfasına geri dön
        return redirect(url_for('debt_status'))

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
    return render_template('edit_payment.html', payment=payment, user_name=session.get('user_name'))



if __name__ == '__main__':
    app.run(debug=True)

