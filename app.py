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
#  git commit -m "Sayfalarda büyük değişiklikler yapıldı"
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

# app.py dosyanızdaki mevcut add_flats fonksiyonunu bu kodla değiştirin.

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
            return redirect(url_for('dashboard'))

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

# app.py dosyanızdaki @app.route('/assign_flat_owner', methods=['GET', 'POST']) fonksiyonunu tamamen bu kodla değiştirin

# app.py dosyanızdaki @app.route('/assign_flat_owner', methods=['GET', 'POST']) fonksiyonunu tamamen bu kodla değiştirin


# app.py'deki assign_flat_owner fonksiyonunu bununla değiştirin
@app.route('/assign_flat_owner', methods=['GET', 'POST'])
def assign_flat_owner():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
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
                flash('Daire sahibi başarıyla atandı! Şimdi ödeme planını oluşturabilirsiniz.', 'success')
                # === YENİ VE AKILLI YÖNLENDİRME ===
                # Kullanıcıyı doğrudan plan yönetim sayfasına yönlendir.
                return redirect(url_for('manage_payment_plan', flat_id=flat_id))
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
        cur.close()
        conn.close()
    
    return render_template('assign_flat_owner.html',
                           projects=projects,
                           flats_data=flats_data,
                           customers=customers,
                           user_name=session.get('user_name'))


@app.route('/debts')
def debt_status():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    try:
        # === YENİ VE GÜÇLÜ SORGULAMA MANTIĞI ===
        # 1. Sahibi olan TÜM daireleri çek
        cur.execute("""
            SELECT 
                f.id, p.name, f.block_name, f.floor, f.flat_no,
                c.first_name, c.last_name, f.total_price
            FROM flats f
            JOIN projects p ON f.project_id = p.id
            JOIN customers c ON f.owner_id = c.id
            WHERE f.owner_id IS NOT NULL
            ORDER BY p.name, f.block_name, f.floor, f.flat_no
        """)
        owned_flats = cur.fetchall()

        # 2. Tüm taksitleri bir sözlüğe al (performans için)
        cur.execute("SELECT flat_id, due_date, amount, is_paid, paid_amount FROM installment_schedule ORDER BY due_date ASC")
        all_installments_raw = cur.fetchall()
        installments_by_flat = {}
        for flat_id, group in groupby(all_installments_raw, key=lambda x: x[0]):
            installments_by_flat[flat_id] = list(group)

        # 3. Tüm ödemeleri bir sözlüğe al (performans için)
        cur.execute("SELECT flat_id, COALESCE(SUM(amount), 0) as total_paid FROM payments GROUP BY flat_id")
        total_payments_by_flat = dict(cur.fetchall())

        # 4. Tüm verileri birleştir
        flats_data = []
        today = date.today()

        for flat_id, project_name, block_name, floor, flat_no, first_name, last_name, total_price in owned_flats:
            flat_info = {
                'flat_id': flat_id,
                'project_name': project_name,
                'customer_name': f"{first_name} {last_name}",
                'flat_details': f"Blok: {block_name or 'N/A'}, Kat: {floor}, No: {flat_no}",
                'flat_total_price': total_price or 0,
                'total_paid': total_payments_by_flat.get(flat_id, Decimal(0)),
                'installments': []
            }
            flat_info['remaining_debt'] = flat_info['flat_total_price'] - flat_info['total_paid']

            current_installments = installments_by_flat.get(flat_id, [])
            for _, due_date, total_amount, is_paid, paid_amount in current_installments:
                status, css_class = ("Ödendi", "table-success") if is_paid else (f"Kısmen Ödendi ({paid_amount} ₺)", "table-warning") if paid_amount > 0 else ("Gecikmiş", "table-danger") if due_date < today else ("Bekleniyor", "table-light")
                flat_info['installments'].append({
                    'total_amount': total_amount, 'remaining_installment_due': total_amount - paid_amount,
                    'due_date': due_date, 'status': status, 'css_class': css_class
                })
            flats_data.append(flat_info)

    except Exception as e:
        flash(f'Borç durumu sayfası yüklenirken bir hata oluştu: {e}', 'danger')
        flats_data = []
    finally:
        cur.close()
        conn.close()

    return render_template('debts.html', flats_data=flats_data, user_name=session.get('user_name'))


# app.py dosyanıza bu iki yeni route'u ekleyin

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


# app.py dosyanıza bu iki yeni route'u ekleyin

@app.route('/project/<int:project_id>/expenses')
def list_expenses(project_id):
    """Belirli bir projenin tüm giderlerini listeler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    # Proje bilgilerini al
    cur.execute("SELECT name, project_type FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('dashboard'))

    # Projeye ait giderleri al
    cur.execute("""
        SELECT id, title, amount, expense_date, description
        FROM expenses
        WHERE project_id = %s
        ORDER BY expense_date DESC
    """, (project_id,))
    expenses = cur.fetchall()
    
    # Projenin toplam giderini hesapla
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
    total_expenses = cur.fetchone()[0]

    cur.close()
    conn.close()

    return render_template('expenses.html', 
                           project=project, 
                           project_id=project_id, 
                           expenses=expenses, 
                           total_expenses=total_expenses)

@app.route('/project/<int:project_id>/expense/new', methods=['GET', 'POST'])
def add_expense(project_id):
    """Belirli bir projeye yeni bir gider ekler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    
    # Proje var mı diye kontrol et
    cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        flash('Proje bulunamadı.', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        title = request.form['title']
        amount = request.form['amount']
        expense_date = request.form['expense_date']
        description = request.form.get('description', '') # Açıklama opsiyonel olabilir

        if not title or not amount or not expense_date:
            flash('Başlık, Tutar ve Tarih alanları zorunludur.', 'danger')
            return redirect(url_for('add_expense', project_id=project_id))

        try:
            cur.execute("""
                INSERT INTO expenses (project_id, title, amount, expense_date, description)
                VALUES (%s, %s, %s, %s, %s)
            """, (project_id, title, amount, expense_date, description))
            conn.commit()
            flash('Gider başarıyla eklendi.', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Gider eklenirken bir hata oluştu: {e}', 'danger')
        finally:
            cur.close()
            conn.close()
        
        return redirect(url_for('list_expenses', project_id=project_id))

    cur.close()
    conn.close()
    # GET isteği için
    return render_template('new_expense.html', project_name=project[0], project_id=project_id)


# app.py dosyanıza bu yeni route'u ekleyin

@app.route('/expense/<int:expense_id>/edit', methods=['GET', 'POST'])
def edit_expense(expense_id):
    """Belirli bir gideri düzenler."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        # Formdan güncellenmiş verileri al
        title = request.form['title']
        amount = request.form['amount']
        expense_date = request.form['expense_date']
        description = request.form.get('description', '')

        try:
            # Veritabanında UPDATE sorgusunu çalıştır
            cur.execute("""
                UPDATE expenses
                SET title = %s, amount = %s, expense_date = %s, description = %s
                WHERE id = %s
            """, (title, amount, expense_date, description, expense_id))
            conn.commit()
            flash('Gider başarıyla güncellendi.', 'success')

            # Güncellemeden sonra doğru proje sayfasına dönebilmek için project_id'yi al
            cur.execute("SELECT project_id FROM expenses WHERE id = %s", (expense_id,))
            project_id = cur.fetchone()[0]
            return redirect(url_for('list_expenses', project_id=project_id))

        except Exception as e:
            conn.rollback()
            flash(f'Gider güncellenirken bir hata oluştu: {e}', 'danger')
        finally:
            cur.close()
            conn.close()
    
    # GET isteği için: Mevcut gider verilerini çek ve formu doldur
    cur.execute("SELECT id, project_id, title, amount, expense_date, description FROM expenses WHERE id = %s", (expense_id,))
    expense = cur.fetchone()
    cur.close()
    conn.close()
    
    if expense is None:
        flash('Düzenlenecek gider bulunamadı.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('edit_expense.html', expense=expense)


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

@app.route('/api/project/<int:project_id>/flats')
def get_flats_for_project(project_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Yetkisiz erişim'}), 401

    conn = get_connection()
    cur = conn.cursor()
    # YENİ: Sorguya `block_name` sütununu ekledik
    cur.execute("""
        SELECT id, flat_no, floor, room_type, block_name 
        FROM flats 
        WHERE project_id = %s AND owner_id IS NOT NULL
        ORDER BY block_name, floor, flat_no
    """, (project_id,))
    flats_raw = cur.fetchall()
    cur.close()
    conn.close()

    # YENİ: Menüde görünecek metni isteğine göre sadeleştirdik ve blok adını ekledik
    flats = [{'id': f[0], 'text': f"Blok: {f[4] or 'Belirtilmemiş'}, Kat: {f[2]}, No: {f[1]}"} for f in flats_raw]
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


# app.py'deki debt_status fonksiyonunu bununla değiştirin

# app.py'deki debt_status fonksiyonunu bununla değiştirin

# @app.route('/debts')
# def debt_status():
#     if 'user_id' not in session:
#         return redirect(url_for('login'))

#     conn = get_connection()
#     cur = conn.cursor()

#     # Sorguya s.paid_amount sütununu ekledik
#     cur.execute("""
#         SELECT 
#             p.name AS project_name, c.first_name, c.last_name, f.id as flat_id, f.flat_no, f.floor,
#             f.total_price AS flat_total_price, s.id AS schedule_id, s.due_date, s.amount, s.is_paid, f.block_name,
#             s.paid_amount
#         FROM installment_schedule s
#         JOIN flats f ON s.flat_id = f.id
#         JOIN projects p ON f.project_id = p.id
#         JOIN customers c ON f.owner_id = c.id
#         WHERE f.project_id IN (SELECT id FROM projects WHERE project_type = 'normal')
#         ORDER BY p.name, f.block_name, f.floor, f.flat_no, s.due_date
#     """)
#     installments_raw = cur.fetchall()

#     cur.execute("SELECT flat_id, COALESCE(SUM(amount), 0) as total_paid FROM payments GROUP BY flat_id")
#     total_payments_by_flat = dict(cur.fetchall())
    
#     cur.close()
#     conn.close()

#     flats_data = []
#     for key, group in groupby(installments_raw, key=lambda x: x[3]): # flat_id'ye göre grupla
#         group_list = list(group)
#         first_item = group_list[0]
        
#         flat_info = {
#             'flat_id': key,
#             'project_name': first_item[0],
#             'customer_name': f"{first_item[1]} {first_item[2]}",
#             'flat_details': f"Blok: {first_item[11] or 'N/A'}, Kat: {first_item[5]}, No: {first_item[4]}",
#             'flat_total_price': first_item[6] or 0,
#             'total_paid': total_payments_by_flat.get(key, 0),
#             'installments': []
#         }
#         flat_info['remaining_debt'] = flat_info['flat_total_price'] - flat_info['total_paid']

#         today = date.today()
#         for item in group_list:
#             due_date, is_paid, total_amount, paid_amount = item[8], item[10], item[9], item[12]
#             status = ""
#             css_class = ""

#             if is_paid:
#                 status = "Ödendi"
#                 css_class = "table-success"
#             elif paid_amount > 0:
#                 status = f"Kısmen Ödendi ({paid_amount} ₺)"
#                 css_class = "table-warning"
#             elif due_date < today:
#                 status = "Gecikmiş"
#                 css_class = "table-danger"
#             else:
#                 status = "Bekleniyor"
#                 css_class = "table-light"
            
#             flat_info['installments'].append({
#                 'due_date': due_date,
#                 'total_amount': total_amount,
#                 'paid_amount': paid_amount,
#                 'remaining_installment_due': total_amount - paid_amount,
#                 'status': status,
#                 'css_class': css_class
#             })
        
#         flats_data.append(flat_info)

#     return render_template('debts.html', flats_data=flats_data, user_name=session.get('user_name'))


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

@app.route('/payments')
def list_payments():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # ... (Filtreleme ve sıralama kodları aynı kalıyor) ...
    project = request.args.get('project')
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    sort_by = request.args.get('sort_by', 'tarih')
    order = request.args.get('order', 'desc')

    sortable_columns = {
        'proje': 'pr.name', 'musteri': 'c.last_name',
        'tarih': 'p.payment_date', 'tutar': 'p.amount'
    }
    order_by_column = sortable_columns.get(sort_by, 'p.payment_date')
    if order not in ['asc', 'desc']: order = 'desc'

    # YENİ: Sorguya f.block_name sütununu ekledik
    sql = """
        SELECT p.id, pr.name, c.first_name, c.last_name, f.flat_no, f.floor,
               p.installment, p.amount, p.payment_date, f.block_name
        FROM payments p
        JOIN flats f ON p.flat_id = f.id
        JOIN customers c ON f.owner_id = c.id
        JOIN projects pr ON f.project_id = pr.id
    """
    filters, params = [], []
    if project:
        filters.append("pr.name = %s")
        params.append(project)
    if start:
        filters.append("p.payment_date >= %s")
        params.append(start)
    if end:
        filters.append("p.payment_date <= %s")
        params.append(end)

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += f" ORDER BY {order_by_column} {order.upper()}"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, tuple(params))
    payments = cur.fetchall()
    
    cur.execute("SELECT name FROM projects ORDER BY name")
    all_projects = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    return render_template('payments.html',
                           payments=payments, all_projects=all_projects,
                           selected_project=project, start_date=start,
                           end_date=end, sort_by=sort_by, order=order,
                           user_name=session.get('user_name'))

@app.route('/reports')
def reports():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    # 1. Tüm projeleri çek
    cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
    projects = cur.fetchall()

    project_summaries = []
    for project_id, project_name, project_type in projects:
        summary = {
            'project_id': project_id,
            'project_name': project_name,
            'project_type': project_type
        }

        if project_type == 'normal':
            # Normal projeler için finansal verileri hesapla
            # Planlanan Toplam Gelir (Ödeme planlarındaki tüm taksitlerin toplamı)
            cur.execute("""
                SELECT COALESCE(SUM(s.amount), 0) FROM installment_schedule s
                JOIN flats f ON s.flat_id = f.id WHERE f.project_id = %s
            """, (project_id,))
            planned_revenue = cur.fetchone()[0]

            # Tahsil Edilen Toplam Gelir (Ödemeler tablosu)
            cur.execute("""
                SELECT COALESCE(SUM(p.amount), 0) FROM payments p
                JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s
            """, (project_id,))
            collected_revenue = cur.fetchone()[0]
            
            # Toplam Gider
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
            total_expenses = cur.fetchone()[0]

            summary.update({
                'planned_revenue': planned_revenue,
                'collected_revenue': collected_revenue,
                'outstanding_debt': planned_revenue - collected_revenue,
                'progress_percentage': (collected_revenue / planned_revenue * 100) if planned_revenue > 0 else 0,
                'total_expenses': total_expenses,
                'net_cash_flow': collected_revenue - total_expenses
            })

        elif project_type == 'cooperative':
            # Kooperatif projeler için finansal verileri hesapla
            # Üyelerden Toplanan (Ödemeler tablosu)
            cur.execute("""
                SELECT COALESCE(SUM(p.amount), 0) FROM payments p
                JOIN flats f ON p.flat_id = f.id WHERE f.project_id = %s
            """, (project_id,))
            member_contributions = cur.fetchone()[0]

            # Toplam Gider
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE project_id = %s", (project_id,))
            total_expenses = cur.fetchone()[0]
            
            summary.update({
                'member_contributions': member_contributions,
                'total_expenses': total_expenses,
                'cash_balance': member_contributions - total_expenses
            })
        
        project_summaries.append(summary)

    cur.close()
    conn.close()

    return render_template('reports.html', 
                           project_summaries=project_summaries,
                           user_name=session.get('user_name'))


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    # Toplam müşteri
    cur.execute("SELECT COUNT(*) FROM customers;")
    total_customers = cur.fetchone()[0]

    # Toplam daire
    cur.execute("SELECT COUNT(*) FROM flats;")
    total_flats = cur.fetchone()[0]

    # Toplam ödeme sayısı
    cur.execute("SELECT COUNT(*) FROM payments;")
    total_payments = cur.fetchone()[0]

    # Toplam ödenen tutar
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM payments;")
    total_amount = cur.fetchone()[0]

    # --- EKSİK OLAN BÖLÜM BURASI ---
    # Proje listesini veritabanından çek
    cur.execute("SELECT id, name, project_type FROM projects ORDER BY name")
    projects_raw = cur.fetchall()
    
    # Ham veriyi sözlük listesine çevirerek şablon için hazırla
    projects = []
    for p in projects_raw:
        projects.append({'id': p[0], 'name': p[1], 'project_type': p[2]})
    # --- EKSİK BÖLÜMÜN SONU ---

    cur.close()
    conn.close()

    return render_template('dashboard.html',
        user_name=session.get('user_name'),
        total_customers=total_customers,
        total_flats=total_flats,
        total_payments=total_payments,
        total_amount=total_amount,
        # Değişkeni şablona gönder
        projects=projects
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
            # === DÜZELTME BURADA: Gelen tutarı float yerine Decimal'e çeviriyoruz ===
            payment_amount = Decimal(request.form.get('amount'))
            payment_date_str = request.form.get('payment_date')
            description = request.form.get('description', 'Müşteri Ödemesi')

            if not all([flat_id, payment_amount, payment_date_str]):
                flash('Lütfen tüm zorunlu alanları doldurun.', 'danger')
                # Hata durumunda GET isteği için gerekli veriyi çekip formu tekrar göster
                cur.execute("SELECT id, name FROM projects ORDER BY name")
                projects = cur.fetchall()
                return render_template('new_payment.html', projects=projects, user_name=session.get('user_name'))

            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()

            cur.execute("""
                INSERT INTO payments (flat_id, amount, payment_date, description)
                VALUES (%s, %s, %s, %s)
            """, (flat_id, payment_amount, payment_date, description))
            
            amount_to_distribute = payment_amount
            cur.execute("""
                SELECT id, amount, paid_amount FROM installment_schedule
                WHERE flat_id = %s AND is_paid = FALSE
                ORDER BY due_date ASC
            """, (flat_id,))
            
            unpaid_installments = cur.fetchall()

            for installment_id, total_amount, paid_amount in unpaid_installments:
                if amount_to_distribute <= 0:
                    break

                remaining_due = total_amount - paid_amount
                
                if amount_to_distribute >= remaining_due:
                    cur.execute("UPDATE installment_schedule SET paid_amount = %s, is_paid = TRUE WHERE id = %s", (total_amount, installment_id))
                    amount_to_distribute -= remaining_due
                else:
                    new_paid_amount = paid_amount + amount_to_distribute
                    cur.execute("UPDATE installment_schedule SET paid_amount = %s WHERE id = %s", (new_paid_amount, installment_id))
                    amount_to_distribute = 0
            
            conn.commit()
            flash(f'{payment_amount:,.2f} ₺ tutarındaki ödeme başarıyla kaydedildi ve borçlara yansıtıldı.', 'success')
            return redirect(url_for('debt_status'))

        except Exception as e:
            if conn:
                conn.rollback()
            flash(f'Ödeme kaydedilirken bir hata oluştu: {e}', 'danger')
            return redirect(url_for('new_payment'))
        finally:
            if conn:
                cur.close()
                conn.close()

    # GET isteği (sayfa ilk açıldığında)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM projects ORDER BY name")
    projects = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('new_payment.html', 
                           projects=projects, 
                           user_name=session.get('user_name'))



if __name__ == '__main__':
    app.run(debug=True)

