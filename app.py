# --------------------
# önce yeni proje oluştur(her projede kat, daire bilgileri girilecek)
# eğer proje türü kooperatif ise bu kooperatif proje türü için sadece daire sahiplerinin ödedikleri ücretler ve kooperatifin giderleri görünecek
# veritabanında her projenin giderleri ayrı bir tabloda tutalacak ve daire sahiplerinin ödedikleri ücretlerin gösteren sayfanın altında farklı bir tabloda görünecek
# eğer proje türü normal ise(kooperatif değilse) daire sahiplerini için ayrı bir sayfadan proje ve isim seçilerek bir ödeme planı girilir   
# ve o kişi her ödeme yaptığında ödeme planından borcu düşülecek, eğer ödeme yapmazsa ödeme planı sayfasında kırmızı renk ile gösterilecek
# her proje türü için giderler sayfası olacak ve bu sayfa tüm ödemeler sayfasının altında görünecek
# giderleri eklemek için yeni ödeme sayfası gibi bir sayfadan ekleme yapılacak
# her müşteri her daire ve her proje için toplam borç, kalan borç ödenecek borç, ödenmeyen borç gibi bilgiler bir tabloda gösterilecek
# filtreleme yapılarak daha detaylı analiz yapıılabilecek
# ana sayfada grafikler vb gösterilecek 
# 
# projeler içinde bloklar olabilir 
# ödeme ekleme, ödeme planı oluşturma vs. tüm ilemlerde daire seçerken proje seçtikten sonra sadece daire kat ve daire nosu gösterilerek seçim yapılsın
# veri ekleme yapılan tüm sayfalrda silme ve düzenleme butonları eklensin
#
# git add .
#  git commit -m "Yeni özellikler eklendi."
#  git push origin main
#  git branch
# --------------------
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify # jsonify import edildiğinden emin olun
from dateutil.relativedelta import relativedelta # Tarih hesaplamaları için
from flask import Flask, render_template, request, redirect, url_for, session, flash
from db import get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from parser import parse_whatsapp_message
import os
from datetime import datetime
from itertools import groupby
from datetime import date
from decimal import Decimal # === YENİ VE EN ÖNEMLİ SATIR: Decimal tipini import et ===


app = Flask(__name__)
app.secret_key = os.urandom(24)  # Oturum verisi için gizli anahtar

# --------------------
# YARDIMCI FONKSİYONLAR
# --------------------

def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, email, password_hash, full_name FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user  # (id, email, password_hash, full_name) veya None

# --------------------
# ROUTELAR
# --------------------
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
        return redirect(url_for('add_flats', project_id=project_id))  # <--- Buraya yönlendir

    return render_template('project_new.html')



# app.py'deki add_flats fonksiyonunu bununla değiştirin

@app.route('/project/<int:project_id>/flats', methods=['GET', 'POST'])
def add_flats(project_id):
    """
    Bir projedeki tüm daireleri yönetir (CRUD).
    POST isteğinde, projenin tüm dairelerini siler ve formdan gelen yeni listeyi kaydeder.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        # Formdan gelen tüm daire verilerini listeler halinde al
        block_names = request.form.getlist('block_name[]')
        flat_nos = request.form.getlist('flat_no[]')
        floors = request.form.getlist('floor[]')
        room_types = request.form.getlist('room_type[]')
        
        try:
            # 1. Önce bu projeye ait tüm mevcut daireleri sil (temiz bir başlangıç için)
            cur.execute("DELETE FROM flats WHERE project_id = %s", (project_id,))
            
            # 2. Formdan gelen güncel listeyi veritabanına yeniden ekle
            for block, flat_no, floor, room_type in zip(block_names, flat_nos, floors, room_types):
                # Sadece dolu satırların kaydedildiğinden emin ol
                if block and flat_no and floor and room_type:
                    cur.execute("""
                        INSERT INTO flats (project_id, block_name, flat_no, floor, room_type)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (project_id, block.strip(), flat_no, floor, room_type.strip()))
            
            conn.commit()
            flash('Daire listesi başarıyla güncellendi.', 'success')
            return redirect(url_for('assign_flat_owner'))

        except Exception as e:
            conn.rollback()
            flash(f'Daireler güncellenirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('add_flats', project_id=project_id))
        finally:
            cur.close()
            conn.close()

    # GET isteği için: Proje adını ve mevcut daireleri çek
    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project = cur.fetchone()
        if not project:
            flash('Proje bulunamadı.', 'danger')
            return redirect(url_for('dashboard'))
        project_name = project[0]
        
        # Mevcut daireleri forma doldurmak için çek
        cur.execute("SELECT block_name, flat_no, floor, room_type FROM flats WHERE project_id = %s ORDER BY block_name, floor, flat_no", (project_id,))
        existing_flats = cur.fetchall()
        
    except Exception as e:
        flash(f'Veri alınırken bir hata oluştu: {e}', 'danger')
        project_name = "Bilinmeyen Proje"
        existing_flats = []
    finally:
        cur.close()
        conn.close()

    return render_template('project_flats.html', 
                           project_id=project_id, 
                           project_name=project_name,
                           existing_flats=existing_flats)


# app.py dosyanıza ekleyin

@app.route('/expenses/select_project', methods=['GET', 'POST'])
def select_project_for_expenses():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        project_id = request.form.get('project_id')
        if project_id:
            return redirect(url_for('list_expenses', project_id=project_id))
        else:
            flash("Lütfen bir proje seçin.", "warning")
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template('select_project_expenses.html', 
                           projects=projects, 
                           user_name=session.get('user_name'))


# app.py'deki mevcut new_supplier_payment fonksiyonunu bu kodla değiştirin.

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
            # === DÜZELTME: Sorguya e.project_id = %s eklendi ===
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
            flash(f'{payment_amount:,.2f} ₺ tutarındaki tedarikçi ödemesi kaydedildi ve borçlara yansıtıldı.', 'success')
            return redirect(url_for('list_expenses', project_id=project_id))

        except Exception as e:
            if conn: conn.rollback()
            flash(f'Gider ödemesi kaydedilirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('new_supplier_payment'))
        finally:
            if conn:
                cur.close()
                conn.close()

    # GET isteği için
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

    # GET isteği için
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


# app.py dosyanızdaki pay_expense_installment fonksiyonunu bununla değiştirin.

@app.route('/expense_installment/<int:installment_id>/pay', methods=['GET', 'POST'])
def pay_expense_installment(installment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date') # İşlem tarihi
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
            
            else: # Nakit ödeme
                # Taksit durumunu doğrudan güncelle
                new_paid_amount = (already_paid or 0) + payment_amount
                is_paid = new_paid_amount >= total_due
                cur.execute("UPDATE expense_schedule SET paid_amount = %s, is_paid = %s WHERE id = %s", (new_paid_amount, is_paid, installment_id))
                flash("Nakit ödeme başarıyla kaydedildi ve borca yansıtıldı.", "success")

            # Fiili ödemeyi supplier_payments'a her zaman kaydet
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

    # GET isteği
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



# app.py dosyanızdaki mevcut assign_flat_owner fonksiyonunu bu kodla değiştirin.

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

                # === YENİ VE AKILLI YÖNLENDİRME MANTIĞI ===
                # Projenin tipini öğrenmek için veritabanını sorgula
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

    # GET isteği için (bu kısım aynı kalıyor)
    try:
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        projects = cur.fetchall()
        cur.execute("""
            SELECT f.id, pr.name, f.flat_no, f.floor, c.first_name, c.last_name, f.owner_id, 
                   f.block_name, c.phone, c.national_id
            FROM flats f
            JOIN projects pr ON f.project_id = pr.id
            LEFT JOIN customers c ON f.owner_id = c.id
            ORDER BY pr.name, f.block_name, f.floor, f.flat_no
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

# app.py dosyanızdaki mevcut debt_status fonksiyonunu bu kodla tamamen değiştirin.

@app.route('/debts')
def debt_status():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    projects_data = []

    try:
        # Adım 1: Sahibi olan TÜM daireleri, proje bilgileriyle birlikte çek.
        cur.execute("""
            SELECT 
                f.id, p.name, p.project_type, f.block_name, f.floor, f.flat_no,
                c.first_name, c.last_name, f.total_price
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            JOIN customers c ON f.owner_id = c.id
            WHERE f.owner_id IS NOT NULL
            ORDER BY p.name, f.block_name, f.floor, f.flat_no
        """)
        owned_flats = cur.fetchall()

        # Adım 2: TÜM taksitleri bir sözlüğe al (Normal projeler için).
        cur.execute("SELECT flat_id, due_date, amount, is_paid, paid_amount FROM installment_schedule ORDER BY flat_id, due_date ASC")
        all_installments_raw = cur.fetchall()
        installments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_installments_raw, key=lambda x: x[0])}

        # Adım 3: TÜM ödemelerin toplamını daireye göre grupla.
        cur.execute("SELECT flat_id, COALESCE(SUM(amount), 0) as total_paid FROM payments GROUP BY flat_id")
        total_payments_by_flat = dict(cur.fetchall())

        # --- DÜZENLEME: Sorguya 'payment_method' eklendi ---
        cur.execute("SELECT flat_id, payment_date, description, amount, payment_method FROM payments ORDER BY flat_id, payment_date DESC")
        all_payments_raw = cur.fetchall()
        payments_by_flat = {flat_id: list(group) for flat_id, group in groupby(all_payments_raw, key=lambda x: x[0])}

        # Adım 4: Tüm verileri Python'da birleştir.
        flats_list = []
        today = date.today()
        for flat_id, project_name, project_type, block_name, floor, flat_no, first_name, last_name, total_price in owned_flats:
            total_paid = total_payments_by_flat.get(flat_id, Decimal(0))
            flat_dict = {
                'flat_id': flat_id,
                'project_name': project_name,
                'project_type': project_type,
                'customer_name': f"{first_name} {last_name}",
                'flat_details': f"Blok: {block_name or 'N/A'}, Kat: {floor}, No: {flat_no}",
                'total_paid': total_paid,
                'installments': [],
                'payments': []
            }
            
            if project_type == 'normal':
                flat_dict['flat_total_price'] = total_price or 0
                flat_dict['remaining_debt'] = flat_dict['flat_total_price'] - total_paid
                
                current_installments = installments_by_flat.get(flat_id, [])
                for inst_id, due_date, total_amount, is_paid, paid_amount in current_installments:
                    paid_amount = paid_amount or Decimal(0)
                    status, css_class = ("Ödendi", "table-success") if is_paid else (f"Kısmen Ödendi", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
                    
                    flat_dict['installments'].append({
                        'id': inst_id, # Taksit ID'sini de ekleyelim
                        'due_date': due_date, 
                        'total_amount': total_amount, 
                        'remaining_installment_due': total_amount - paid_amount,
                        'status': status, 
                        'css_class': css_class
                    })
            
            # --- DÜZENLEME: Ödeme detayları artık hem normal hem de kooperatif projeler için ekleniyor ---
            flat_dict['payments'] = payments_by_flat.get(flat_id, [])
            
            flats_list.append(flat_dict)

        # Son adım: Daireleri projelere göre grupla
        for key, group in groupby(flats_list, key=lambda x: x['project_name']):
            group_list = list(group)
            projects_data.append({
                'project_name': key,
                'project_type': group_list[0]['project_type'],
                'flats': group_list
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


# app.py dosyanıza bu iki yeni route'u ekleyin

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
            
            # YENİ: Başarılı güncelleme sonrası yönlendirme mantığı
            flash('Proje başarıyla güncellendi. Şimdi daire bilgilerini gözden geçirebilirsiniz.', 'success')
            return redirect(url_for('add_flats', project_id=project_id))

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
    """Bir projeyi ve ona bağlı tüm verileri siler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    try:
        # Veritabanındaki ON DELETE CASCADE ayarı sayesinde, bu projeye bağlı
        # tüm daireler, giderler, taksit planları ve ödemeler otomatik olarak silinecektir.
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        conn.commit()
        flash('Proje ve ilgili tüm veriler başarıyla silindi.', 'success')
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
        # 1. Dairenin sahibini, toplam fiyatını ve taksit sayısını NULL yap
        # === GÜNCELLEME BURADA ===
        cur.execute("""
            UPDATE flats
            SET owner_id = NULL, total_price = NULL, total_installments = NULL
            WHERE id = %s
        """, (flat_id,))
        
        # 2. Bu daireye ait tüm ödeme kayıtlarını sil
        cur.execute("""
            DELETE FROM payments
            WHERE flat_id = %s
        """, (flat_id,))

        # 3. Bu daireye ait tüm ödeme planı kayıtlarını sil
        # (Bu aslında gereksiz çünkü flats.id'ye ON DELETE CASCADE bağlı,
        # ama açıkça belirtmekte zarar yok)
        cur.execute("""
            DELETE FROM installment_schedule
            WHERE flat_id = %s
        """, (flat_id,))
        
        conn.commit()
        return jsonify({'success': True, 'message': 'Daire sahibi ve ilgili tüm finansal veriler başarıyla sıfırlandı.'})

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


# app.py dosyanızdaki list_checks fonksiyonunu bununla değiştirin

@app.route('/checks')
def list_checks():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    
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

    except Exception as e:
        flash(f"Çekler listelenirken bir hata oluştu: {e}", "danger")
        incoming_checks, outgoing_checks = [], []
    finally:
        cur.close()
        conn.close()

    # === DÜZELTME: Bugünün tarihini şablona gönder ===
    return render_template('checks.html', 
                           incoming_checks=incoming_checks,
                           outgoing_checks=outgoing_checks,
                           user_name=session.get('user_name'),
                           today=date.today())

# app.py dosyanıza bu yeni route'u ekleyin.

# app.py dosyanızdaki mevcut update_check_status fonksiyonunu bu kodla tamamen değiştirin.

@app.route('/check/update_status', methods=['POST'])
def update_check_status():
    """Bir çeki 'tahsil edildi', 'ödendi' veya 'karşılıksız' olarak işaretler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    check_id = request.form.get('check_id')
    check_type = request.form.get('check_type') # 'incoming' veya 'outgoing'
    new_status = request.form.get('new_status')

    conn = get_connection()
    cur = conn.cursor()

    try:
        if check_type == 'incoming':
            # --- ALINAN MÜŞTERİ ÇEKİ İŞLEMLERİ (Bu kısım zaten doğru çalışıyor) ---
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
                flash(f'{payment_amount:,.2f} ₺ tutarındaki çek tahsil edildi ve borca yansıtıldı.', 'success')

        elif check_type == 'outgoing':
            # === YENİ VE DÜZELTİLMİŞ VERİLEN ÇEK MANTIĞI ===
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
                flash(f'{payment_amount:,.2f} ₺ tutarındaki çek ödendi ve gider borcuna yansıtıldı.', 'success')
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


# app.py dosyanıza bu iki yeni route'u ekleyin.

@app.route('/reports/cooperative/select', methods=['GET', 'POST'])
def select_project_for_coop_report():
    """Kooperatif raporu için proje seçim sayfası."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        project_id = request.form.get('project_id')
        if project_id:
            # Seçilen ay ve yılı da alıp rapora yönlendir
            report_month = request.form.get('report_month') # YYYY-MM formatında
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

# app.py dosyanızdaki cooperative_report fonksiyonunu bununla değiştirin.

@app.route('/reports/cooperative/<int:project_id>/<int:year>/<int:month>')
def cooperative_report(project_id, year, month):
    """Belirli bir kooperatif projesinin aylık finansal raporunu gösterir."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    report_data = {}

    # --- EKLEME BAŞLANGICI: Türkçe ay isimleri için sözlük ---
    turkish_months = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
        7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
    }
    # --- EKLEME SONU ---

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
        expense_details.sort(key=lambda x: x[0]) # Birleşik listeyi tarihe göre sırala
        report_data['expense_details'] = expense_details
        
        # --- DEĞİŞİKLİK: Rapor periyodu Türkçe ay ismini kullanarak oluşturuluyor ---
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


# app.py dosyanızdaki mevcut list_expenses fonksiyonunu bu kodla tamamen değiştirin.

@app.route('/project/<int:project_id>/expenses')
def list_expenses(project_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    expenses_data = []
    petty_cash_items = []
    project_name = ""
    total_project_expense = Decimal(0)

    try:
        cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name_result = cur.fetchone()
        if not project_name_result:
            flash("Proje bulunamadı.", "danger")
            return redirect(url_for('dashboard'))
        project_name = project_name_result[0]

        # 1. Planlı/Büyük Giderleri Çek
        cur.execute("""
            SELECT e.id, e.title, e.amount, e.description, s.name as supplier_name
            FROM expenses e
            LEFT JOIN suppliers s ON e.supplier_id = s.id
            WHERE e.project_id = %s
            ORDER BY e.id DESC
        """, (project_id,))
        expenses_raw = cur.fetchall()

        # 2. Planlı Giderlerin Taksitlerini Çek
        cur.execute("""
            SELECT expense_id, due_date, sch.amount, is_paid, paid_amount, sch.id as installment_id
            FROM expense_schedule sch
            JOIN expenses e ON sch.expense_id = e.id
            WHERE e.project_id = %s
            ORDER BY expense_id, sch.due_date ASC
        """, (project_id,))
        schedules_by_expense = {k: list(v) for k, v in groupby(cur.fetchall(), key=lambda x: x[0])}

        # 3. YENİ: Küçük Giderleri (Petty Cash) Çek
        cur.execute("""
            SELECT id, title, amount, expense_date, description
            FROM petty_cash_expenses
            WHERE project_id = %s
            ORDER BY expense_date DESC, id DESC
        """, (project_id,))
        petty_cash_items = cur.fetchall()

        # 4. Projenin Toplam Giderini Doğru Hesapla
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
        total_planned_expense = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
        total_petty_cash = cur.fetchone()[0]
        total_project_expense = total_planned_expense + total_petty_cash

        # 5. Planlı gider verilerini işle
        today = date.today()
        for expense_id, title, total_amount, description, supplier_name in expenses_raw:
            schedule = schedules_by_expense.get(expense_id, [])
            total_paid_for_this_expense = sum(item[4] for item in schedule if item[4] is not None)
            expense_dict = {
                'expense_id': expense_id, 'title': title, 'supplier_name': supplier_name or "Tedarikçi Belirtilmemiş",
                'total_amount': total_amount, 'total_paid': total_paid_for_this_expense,
                'remaining_due': total_amount - total_paid_for_this_expense, 'installments': []
            }
            for _, due_date, inst_amount, is_paid, paid_amount, inst_id in schedule:
                paid_amount = paid_amount or Decimal(0)
                status, css_class = ("Ödendi", "table-success") if is_paid else (f"Kısmen Ödendi", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
                expense_dict['installments'].append({
                    'id': inst_id, 'due_date': due_date, 'total_amount': inst_amount,
                    'remaining_installment_due': inst_amount - paid_amount,
                    'status': status, 'css_class': css_class, 'is_paid': is_paid
                })
            expenses_data.append(expense_dict)
            
    except Exception as e:
        flash(f"Giderler listelenirken bir hata oluştu: {e}", "danger")
        print(f"EXPENSES PAGE ERROR: {e}")
    finally:
        cur.close()
        conn.close()

    return render_template('expenses.html',
                           project_id=project_id,
                           project_name=project_name,
                           expenses_data=expenses_data,
                           petty_cash_items=petty_cash_items,
                           total_project_expense=total_project_expense,
                           user_name=session.get('user_name'))



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

    # GET isteği için
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



# app.py dosyanıza bu yeni route'u ekleyin

@app.route('/expense/<int:expense_id>/delete', methods=['POST'])
def delete_expense(expense_id):
    """Belirli bir gideri veritabanından siler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Silme işleminden sonra doğru proje sayfasına dönebilmek için proje_id'yi formdan alıyoruz.
    project_id = request.form.get('project_id')

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM expenses WHERE id = %s", (expense_id,))
        conn.commit()
        flash('Gider başarıyla silindi.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Gider silinirken bir hata oluştu: {e}', 'danger')
    finally:
        cur.close()
        conn.close()

    # Kullanıcıyı silme işlemini yaptığı projenin giderler sayfasına geri yönlendir.
    if project_id:
        return redirect(url_for('list_expenses', project_id=project_id))
    else:
        # Eğer bir şekilde project_id gelmezse, ana sayfaya yönlendir.
        return redirect(url_for('dashboard'))

# app.py'deki get_flats_for_project fonksiyonunu bununla değiştirin

# app.py dosyanızdaki get_flats_for_project fonksiyonunu bununla değiştirin.

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
    
    # YENİ: Sorguya `customers` tablosu eklendi (JOIN ile)
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

    # YENİ: Daire metnine sahip adı eklendi
    flats = [{
        'id': f[0], 
        'text': f"Blok: {f[4] or 'N/A'}, Kat: {f[2]}, No: {f[1]}  —  ({f[5]} {f[6]})"
    } for f in flats_raw]
    
    return jsonify(flats)

"""
@app.route('/payment_plan/new', methods=['GET', 'POST'])
def new_payment_plan():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            flat_id = int(request.form['flat_id'])
            
            # Dinamik olarak eklenen taksit verilerini listeler halinde al
            due_dates = request.form.getlist('due_date[]')
            amounts = request.form.getlist('amount[]')

            if not due_dates or not amounts or len(due_dates) != len(amounts):
                flash('Lütfen en az bir geçerli taksit girin.', 'danger')
                return redirect(url_for('new_payment_plan'))

            # Girilen tutarları sayısal değere çevir ve toplam borcu hesapla
            numeric_amounts = [float(a) for a in amounts]
            total_price = sum(numeric_amounts)
            total_installments = len(numeric_amounts)
            
            # --- Veritabanı İşlemleri ---
            # 1. Bu daireye ait eski bir ödeme planı varsa temizle
            cur.execute("DELETE FROM installment_schedule WHERE flat_id = %s", (flat_id,))
            
            # 2. Dairenin toplam fiyat ve taksit sayısını yeni hesaplanan değerlerle güncelle
            cur.execute("UPDATE flats SET total_price = %s, total_installments = %s WHERE id = %s",
                        (total_price, total_installments, flat_id))

            # 3. Yeni taksit planını satır satır ekle
            for date_str, amount in zip(due_dates, numeric_amounts):
                due_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                cur.execute("""
                    #INSERT INTO installment_schedule (flat_id, due_date, amount, is_paid)
                    #VALUES (%s, %s, %s, %s)
                #""", (flat_id, due_date, amount, False))
"""
            conn.commit()
            flash(f'{total_installments} taksitten oluşan yeni ödeme planı başarıyla oluşturuldu!', 'success')
            return redirect(url_for('debt_status'))

        except Exception as e:
            conn.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')
            print(f"HATA: Manuel ödeme planı oluşturulurken: {e}")
        finally:
            cur.close()
            conn.close()
            # GET isteğinde hata olursa veya POST sonrası yönlendirme olmazsa diye
            # projeleri tekrar çekmek güvenli olabilir, ancak şimdilik bu şekilde bırakıyoruz.
            # Normalde POST bloğunun sonunda her zaman redirect olmalı.
            
    # GET isteği için (bu kısım aynı kalıyor)
    cur.execute("SELECT id, name FROM projects WHERE project_type = 'normal' ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template('new_payment_plan.html', projects=projects)
"""

# app.py dosyanızdaki new_payment_plan fonksiyonunu silip bunu ekleyin.

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

    # GET isteği için: Mevcut planı ve daire bilgilerini çek
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


# app.py dosyanıza bu yeni route'u ekleyin

# app.py dosyanızdaki print_debt_statement fonksiyonunu bununla değiştirin.

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
        # === GÜNCELLEME BURADA: Kısmi ödeme mantığı eklendi ===
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


# app.py dosyanızdaki mevcut list_payments fonksiyonunu bu kodla değiştirin.

# app.py'deki list_payments fonksiyonunu bununla değiştirin

# app.py dosyanızdaki mevcut list_payments fonksiyonunu bu kodla değiştirin.

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

    # --- DÜZENLEME: SQL sorgusuna 'p.payment_method' eklendi ---
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

# app.py dosyanızdaki mevcut reports fonksiyonunu bu kodla değiştirin.

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

            # --- YENİ EKLENEN ORTAK BİLGİLER ---
            cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s", (project_id,))
            summary['total_flats'] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(id) FROM flats WHERE project_id = %s AND owner_id IS NOT NULL", (project_id,))
            summary['assigned_flats'] = cur.fetchone()[0]

            # --- DÜZELTME BAŞLANGICI: Gider sayısı ve toplamı doğru hesaplanıyor ---
            # 1. Gider sayısını her iki tablodan toplayarak hesapla
            cur.execute("SELECT COUNT(id) FROM expenses WHERE project_id = %s", (project_id,))
            large_expense_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(id) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
            petty_cash_count = cur.fetchone()[0]
            summary['expense_count'] = large_expense_count + petty_cash_count

            # 2. Gider toplamını her iki tablodan toplayarak hesapla
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
            total_large_expenses = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM petty_cash_expenses WHERE project_id = %s", (project_id,))
            total_petty_cash = cur.fetchone()[0]
            total_expenses = total_large_expenses + total_petty_cash
            # --- DÜZELTME SONU ---

            if project_type == 'normal':
                # --- MEVCUT FİNANSAL BİLGİLER ---
                cur.execute("SELECT COALESCE(SUM(s.amount), 0) FROM installment_schedule s JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s", (project_id,))
                planned_revenue = cur.fetchone()[0]
                cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s", (project_id,))
                collected_revenue = cur.fetchone()[0]
                # total_expenses zaten yukarıda doğru bir şekilde hesaplandı.

                summary.update({
                    'planned_revenue': planned_revenue,
                    'collected_revenue': collected_revenue,
                    'outstanding_debt': planned_revenue - collected_revenue,
                    'progress_percentage': (collected_revenue / planned_revenue * 100) if planned_revenue > 0 else 0,
                    'total_expenses': total_expenses, # Düzeltilmiş toplam gider kullanılıyor
                    'net_cash_flow': collected_revenue - total_expenses
                })
                
                # --- YENİ EKLENEN NORMAL PROJE BİLGİLERİ ---
                cur.execute("SELECT COUNT(s.id) FROM installment_schedule s JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s AND s.is_paid = TRUE", (project_id,))
                summary['paid_installments'] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(s.id) FROM installment_schedule s JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s AND s.is_paid = FALSE AND s.due_date < %s", (project_id, today))
                summary['overdue_installments'] = cur.fetchone()[0]

            elif project_type == 'cooperative':
                # --- MEVCUT FİNANSAL BİLGİLER ---
                cur.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s", (project_id,))
                member_contributions = cur.fetchone()[0]
                # total_expenses zaten yukarıda doğru bir şekilde hesaplandı.

                summary.update({
                    'member_contributions': member_contributions,
                    'total_expenses': total_expenses, # Düzeltilmiş toplam gider kullanılıyor
                    'cash_balance': member_contributions - total_expenses
                })
            
            project_summaries.append(summary)

    except Exception as e:
        flash(f"Raporlar oluşturulurken bir hata oluştu: {e}", "danger")
        project_summaries = []
    finally:
        cur.close()
        conn.close()

    return render_template('reports.html', 
                           project_summaries=project_summaries,
                           user_name=session.get('user_name'))


# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.

# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.
# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.

# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.

# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.

# app.py dosyanızdaki mevcut dashboard fonksiyonunu bu kodla tamamen değiştirin.

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


# app.py dosyanıza bu yeni route'u ekleyin

@app.route('/api/monthly_payments')
def monthly_payments_api():
    """Son 12 ayın aylık toplam ödemelerini JSON formatında döndürür."""
    if 'user_id' not in session:
        return jsonify({'error': 'Yetkisiz erişim'}), 401

    conn = get_connection()
    cur = conn.cursor()

    # Son 12 ayın verisini çekmek için veritabanına özel bir sorgu gönderiyoruz.
    # Bu sorgu, her ayın başlangıcını ve o aydaki toplam ödemeyi hesaplar.
    # `DATE_TRUNC('month', ...)` fonksiyonu tarihi ayın ilk gününe yuvarlar.
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

    # Veritabanından gelen veriyi grafiğin beklediği formata dönüştürelim
    # Örnek: labels = ["Ocak", "Şubat", ...], data = [5000, 7500, ...]
    
    # Son 12 ayın tam listesini oluşturalım (veri olmayan aylar için 0 değeri)
    labels = []
    data = []
    today = date.today()
    
    # Veritabanından gelen sonuçları kolay erişim için bir sözlüğe dönüştür
    db_data = {row[0]: float(row[1]) for row in results}

    for i in range(12):
        # Ay hesaplaması: 11 ay öncesinden başlayarak bugüne kadar gel
        month_date = (today - relativedelta(months=11 - i))
        month_start = month_date.replace(day=1)
        
        # Ay ismini Türkçe olarak formatla (Örn: "Temmuz 2024")
        # Bu formatlama için locale ayarlarının sunucuda doğru olması gerekebilir.
        # Alternatif olarak manuel bir ay listesi de kullanılabilir.
        # Ay ismini standart ISO formatında gönder (YYYY-AA-GG)
        labels.append(month_start.isoformat()) # Örn: 2025-07-01
        
        # O ay için veri varsa al, yoksa 0 olarak ata
        data.append(db_data.get(month_start, 0))

    return jsonify({'labels': labels, 'data': data})

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

# app.py dosyanızdaki mevcut new_payment fonksiyonunu bu kodla değiştirin.

# app.py'deki mevcut new_payment fonksiyonunu bu kodla tamamen değiştirin.

# === DÜZELTİLMİŞ new_payment FONKSİYONU ===
# Sadece new_payment fonksiyonunu güncelliyoruz.

# app.py dosyanızdaki mevcut new_payment fonksiyonunu bu kodla değiştirin.

# app.py dosyanızdaki mevcut new_payment fonksiyonunu bu kodla değiştirin.

# app.py dosyanızdaki mevcut new_payment fonksiyonunu bu kodla değiştirin.

# app.py dosyanızdaki mevcut new_payment fonksiyonunu bu kodla değiştirin.

@app.route('/payment/new', methods=['GET', 'POST'])
def new_payment():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        conn = None
        try:
            conn = get_connection()
            cur = conn.cursor()

            flat_id = int(request.form.get('flat_id'))
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date') # Bu artık 'işlem tarihi'
            description = request.form.get('description', '')
            payment_method = request.form.get('payment_method', 'nakit')

            if not all([flat_id, payment_amount, payment_date_str]):
                flash('Lütfen tüm zorunlu alanları doldurun.', 'danger')
                return redirect(url_for('new_payment'))

            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()

            # Daire sahibinin kim olduğunu bul (customer_id)
            cur.execute("SELECT owner_id FROM flats WHERE id = %s", (flat_id,))
            customer_id_result = cur.fetchone()
            if not customer_id_result:
                flash('Daire sahibi bulunamadı.', 'danger')
                return redirect(url_for('new_payment'))
            customer_id = customer_id_result[0]

            if payment_method == 'nakit':
                # --- NAKİT ÖDEME MANTIĞI (ESKİ SİSTEM) ---
                cur.execute(
                    "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method) VALUES (%s, %s, %s, %s, %s)",
                    (flat_id, payment_amount, payment_date, description or 'Nakit Ödeme', 'nakit')
                )
                
                # Parayı taksitlere dağıt
                amount_to_distribute = payment_amount
                cur.execute("SELECT id, amount, paid_amount FROM installment_schedule WHERE flat_id = %s AND is_paid = FALSE ORDER BY due_date ASC", (flat_id,))
                unpaid_installments = cur.fetchall()
                for inst_id, total_amount, paid_amount in unpaid_installments:
                    if amount_to_distribute <= 0: break
                    remaining_due = total_amount - paid_amount
                    if amount_to_distribute >= remaining_due:
                        cur.execute("UPDATE installment_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, inst_id))
                        amount_to_distribute -= remaining_due
                    else:
                        new_paid_amount = paid_amount + amount_to_distribute
                        cur.execute("UPDATE installment_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, inst_id))
                        amount_to_distribute = 0
                
                flash(f'{payment_amount:,.2f} ₺ tutarındaki nakit ödeme kaydedildi ve borca yansıtıldı.', 'success')

            elif payment_method == 'çek':
                # --- YENİ ÇEK ÖDEME MANTIĞI ---
                due_date_str = request.form.get('check_due_date')
                bank_name = request.form.get('check_bank_name')
                check_number = request.form.get('check_number')

                if not due_date_str:
                    flash('Çek ödemesi için Vade Tarihi zorunludur.', 'danger')
                    return redirect(url_for('new_payment'))

                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()

                # 1. Çeki `checks` tablosuna kaydet
                cur.execute(
                    "INSERT INTO checks (customer_id, bank_name, check_number, amount, issue_date, due_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (customer_id, bank_name, check_number, payment_amount, payment_date, due_date)
                )
                check_id = cur.fetchone()[0]

                # 2. `payments` tablosuna bu çekin kaydını oluştur
                cur.execute(
                    "INSERT INTO payments (flat_id, amount, payment_date, description, payment_method, check_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (flat_id, payment_amount, payment_date, description or f'{bank_name} - {check_number} Nolu Çek', 'çek', check_id)
                )
                
                flash(f'Vadesi {due_date.strftime("%d.%m.%Y")} olan {payment_amount:,.2f} ₺ tutarındaki çek başarıyla portföye eklendi.', 'success')

            conn.commit()
            return redirect(url_for('debt_status'))

        except Exception as e:
            if conn: conn.rollback()
            flash(f'Ödeme kaydedilirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('new_payment'))
        finally:
            if conn:
                cur.close()
                conn.close()

    # GET isteği
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('new_payment.html', projects=projects, user_name=session.get('user_name'))




if __name__ == '__main__':
    app.run(debug=True)

