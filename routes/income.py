"""The income side: flat owners, payment plans, debts, customers and
customer payments."""
from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from db import get_connection
from datetime import date, datetime
from itertools import groupby, zip_longest
import json
from decimal import Decimal
from core import app
from helpers import (
    ValidationError, format_thousands, log_audit, claim_submission_token,
    get_page_number, page_window, build_pager, split_page, safe_next,
    login_required, json_login_required,
)
from reconcile import reconcile_customer_payments


# assign_flat_owner fonksiyonu

@app.route('/assign_flat_owner', methods=['GET', 'POST'])
@login_required
def assign_flat_owner():
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot create the same customer
            # twice when the "new customer" option is used.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'assign_flat_owner'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(url_for('assign_flat_owner'))

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

        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception:
            conn.rollback()
            # flat_id may not exist yet if int() failed, so read the raw form.
            app.logger.exception('Failed to assign owner to flat %s',
                                 request.form.get('flat_id'))
            flash('Daire sahibi atanırken bir hata oluştu. Kayıt yapılmadı, '
                  'lütfen bilgileri kontrol edip tekrar deneyin.', 'danger')
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
    except Exception:
        app.logger.exception('Failed to load the assign flat owner page')
        flash('Sayfa verileri alınırken bir hata oluştu. Listeler eksik olabilir.',
              'danger')
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

def _load_debt_status(cur, project_filter, flat_filter, search_query):
    """Everything the /debts page and its Excel export show.

    Both read it from here, so the file always holds what the screen shows.
    Returns a dict: projects_data, all_projects, selected_project_name and
    selected_flat_desc.
    """
    projects_data = []
    selected_project_name = None
    selected_flat_desc = None

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
    if search_query:
        like = f"%{search_query}%"
        flats_sql += (" AND (c.first_name ILIKE %s OR c.last_name ILIKE %s"
                      " OR (c.first_name || ' ' || c.last_name) ILIKE %s)")
        flats_params.extend([like, like, like])
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
    # Column order, read by index in the template. list_customers builds a
    # similar list with the first two columns the other way round, so these
    # two queries must never be copied between each other.
    #   0 p.id            1 p.flat_id      2 p.payment_date  3 p.description
    #   4 p.amount        5 p.payment_method
    #   6 c.status        7 c.bank_name    8 c.check_number   9 c.due_date
    #  10 p.check_id
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
    payment_rows_id_first = cur.fetchall()
    payments_by_flat = {flat_id: list(group) for flat_id, group in groupby(payment_rows_id_first, key=lambda x: x[1])}

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


    return {
        'projects_data': projects_data,
        'all_projects': all_projects,
        'selected_project_name': selected_project_name,
        'selected_flat_desc': selected_flat_desc,
    }


@app.route('/debts')
@login_required
def debt_status():
    conn = get_connection()
    cur = conn.cursor()
    project_filter = request.args.get('project_id', type=int)
    flat_filter = request.args.get('flat_id', type=int)
    search_query = (request.args.get('search') or '').strip()

    try:
        data = _load_debt_status(cur, project_filter, flat_filter,
                                 search_query)
    except Exception:
        app.logger.exception('Failed to build the debt status page')
        flash('Borç durumu sayfası yüklenirken bir hata oluştu. Liste eksik '
              'olabilir.', 'danger')
        data = {'projects_data': [], 'all_projects': [],
                'selected_project_name': None, 'selected_flat_desc': None}
    finally:
        cur.close()
        conn.close()

    return render_template('debts.html',
                           projects_data=data['projects_data'],
                           all_projects=data['all_projects'],
                           selected_project_id=project_filter,
                           selected_project_name=data['selected_project_name'],
                           selected_flat_id=flat_filter,
                           selected_flat_desc=data['selected_flat_desc'],
                           user_name=session.get('user_name'))

@app.route('/delete_flat_owner_data', methods=['POST'])
@json_login_required({'success': False, 'message': 'Yetkisiz erişim'})
def delete_flat_owner_data():
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
@login_required
def list_customers():
    conn = get_connection()
    cur = conn.cursor()
    
    search_query = request.args.get('search', '').strip()
    page = get_page_number()
    customers_pager = build_pager(page, has_next=False)

    try:
        # 1. Müşterileri Çek
        sql_customers = "SELECT id, first_name, last_name, phone, national_id FROM customers"
        params = []
        if search_query:
            sql_customers += " WHERE first_name ILIKE %s OR last_name ILIKE %s"
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        sql_customers += " ORDER BY first_name, last_name"

        limit, offset = page_window(page)
        sql_customers += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql_customers, params)
        customers_raw, customers_pager = split_page(cur.fetchall(), page)

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
        # Column order, read by index in the template. debt_status builds a
        # similar list with the first two columns the other way round and one
        # extra column, so these two queries must never be copied between each
        # other.
        #   0 p.flat_id       1 p.id           2 p.payment_date  3 p.description
        #   4 p.amount        5 p.payment_method
        #   6 c.status        7 c.bank_name    8 c.check_number   9 c.due_date
        cur.execute("""
            SELECT 
                p.flat_id, p.id, p.payment_date, p.description, p.amount, p.payment_method,
                c.status, c.bank_name, c.check_number, c.due_date
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            ORDER BY p.flat_id, p.payment_date DESC
        """)
        payment_rows_flat_first = cur.fetchall()
        payments_history_dict = {k: list(v) for k, v in groupby(payment_rows_flat_first, key=lambda x: x[0])}

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

    except Exception:
        app.logger.exception('Failed to list customers')
        flash("Müşteriler yüklenirken bir hata oluştu. Liste eksik olabilir.",
              "danger")
        customers_data = []
    finally:
        cur.close()
        conn.close()

    return render_template('customers.html', customers_data=customers_data,
                           customers_pager=customers_pager,
                           user_name=session.get('user_name'))


# app.py dosyasındaki manage_payment_plan fonksiyonunu bununla değiştirin:

@app.route('/flat/<int:flat_id>/manage_plan', methods=['GET', 'POST'])
@login_required
def manage_payment_plan(flat_id):
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            next_url = safe_next(request.form.get('next') or request.args.get('next'))
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
                    raise ValidationError(f"Geçersiz tarih: {date_str}")

                cleaned = amount_str.replace(' ', '').replace('.', '').replace(',', '.')
                try:
                    amount = Decimal(cleaned)
                except Exception:
                    raise ValidationError(f"Geçersiz tutar: {amount_str}")

                parsed_rows.append((due_date, amount))

            if not parsed_rows:
                raise ValidationError("En az bir taksit girilmelidir.")

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

        except ValidationError as e:
            conn.rollback()
            flash(str(e), 'danger')
            # Back to the plan form, which shows the message and keeps the
            # 'next' address for the save that follows. Going on to next_url
            # here would lose the message: /debts does not render flashes.
            return redirect(url_for('manage_payment_plan', flat_id=flat_id,
                                    next=next_url or None))
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to update the payment plan of flat %s',
                                 flat_id)
            flash('Ödeme planı güncellenirken bir hata oluştu. Plan '
                  'değiştirilmedi.', 'danger')
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
@login_required
def print_debt_statement(flat_id):
    """Belirli bir dairenin borç dökümünü yazdırma için hazırlar."""
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

        # 3. Daire için yapılan toplam ödemeyi çek. Only cash and cleared
        # checks count. A check in the portfolio or a bounced one closes
        # no debt, so it must stay out of this total.
        cur.execute("""
            SELECT COALESCE(SUM(p.amount), 0)
            FROM payments p
            LEFT JOIN checks c ON p.check_id = c.id
            WHERE p.flat_id = %s
              AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi')
        """, (flat_id,))
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

    except Exception:
        app.logger.exception('Failed to build the debt statement of flat %s',
                             flat_id)
        flash("Döküm oluşturulurken bir hata oluştu.", "danger")
        return redirect(url_for('debt_status'))
    finally:
        cur.close()
        conn.close()


# list_payments fonksiyonu

def _payments_sort(args):
    """(sort_by, order, SQL column) for the /payments list."""
    sort_by = args.get('sort_by', 'tarih')
    order = args.get('order', 'desc')

    sortable_columns = {
        'proje': 'pr.name', 'musteri': 'c.last_name',
        'tarih': 'p.payment_date', 'tutar': 'p.amount'
    }
    order_by_column = sortable_columns.get(sort_by, 'p.payment_date')
    if order not in ['asc', 'desc']: order = 'desc'
    return sort_by, order, order_by_column


def _payments_query(args):
    """The /payments query with its filters and sort order, but no paging.

    The list page adds LIMIT/OFFSET to it. The Excel export runs it as it is.
    Both build their SQL here, so a filter can never mean something different
    on the screen and in the file.
    """
    project = args.get('project')
    start = args.get('start_date')
    end = args.get('end_date')
    customer_id = args.get('customer_id')
    search = (args.get('search') or '').strip()
    _, order, order_by_column = _payments_sort(args)

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
    if search:
        like = f"%{search}%"
        filters.append("(c.first_name ILIKE %s OR c.last_name ILIKE %s"
                       " OR (c.first_name || ' ' || c.last_name) ILIKE %s"
                       " OR p.description ILIKE %s)")
        params.extend([like, like, like, like])

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += f" ORDER BY {order_by_column} {order.upper()}"
    return sql, params


@app.route('/payments')
@login_required
def list_payments():
    # Filtreleme parametreleri
    project = request.args.get('project')
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    customer_id = request.args.get('customer_id')

    # Sıralama parametreleri
    sort_by, order, _ = _payments_sort(request.args)

    sql, params = _payments_query(request.args)

    page = get_page_number()
    limit, offset = page_window(page)
    sql += " LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        payments, payments_pager = split_page(cur.fetchall(), page)
    
        cur.execute("SELECT name FROM projects ORDER BY name")
        all_projects = [r[0] for r in cur.fetchall()]
    
        cur.execute("SELECT id, first_name, last_name FROM customers ORDER BY first_name, last_name")
        all_customers = cur.fetchall()
    
    finally:
        cur.close()
        conn.close()

    return render_template('payments.html',
                           payments=payments,
                           payments_pager=payments_pager,
                           all_projects=all_projects,
                           all_customers=all_customers,
                           selected_project=project,
                           selected_customer_id=customer_id,
                           start_date=start,
                           end_date=end,
                           sort_by=sort_by,
                           order=order,
                           user_name=session.get('user_name'))

@app.route('/payment/new', defaults={'installment_id': None}, methods=['GET', 'POST'])
@app.route('/payment/new/<int:installment_id>', methods=['GET', 'POST'])
@login_required
def new_payment(installment_id):
    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        # ... (POST kısmı aynı kalıyor, DOKUNMAYIN) ...
        try:
            # Double submit guard. It runs before any write and on this same
            # transaction, so a second click cannot create a second payment.
            if not claim_submission_token(cur, request.form.get('submission_token'), 'new_payment'):
                conn.rollback()
                flash('Bu işlem zaten kaydedilmişti.', 'info')
                return redirect(request.form.get('next') or request.args.get('next') or url_for('debt_status'))

            next_url = safe_next(request.form.get('next') or request.args.get('next'))
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
        except Exception:
            conn.rollback()
            app.logger.exception('Failed to save payment (installment_id=%s)',
                                 installment_id)
            flash('Ödeme kaydedilirken bir hata oluştu. Ödeme kaydedilmedi.',
                  'danger')
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
                           
# GÜNCELLENMİŞ FONKSİYON: delete_payment
@app.route('/payment/<int:payment_id>/delete', methods=['POST'])
@login_required
def delete_payment(payment_id):
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
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to delete payment %s', payment_id)
        flash('Ödeme silinirken bir hata oluştu. Ödeme silinmedi, taksit '
              'durumları değişmedi.', 'danger')
    finally:
        cur.close()
        conn.close()
    # debt_status sayfasına geri dön
    return redirect(url_for('debt_status'))

# GÜNCELLENMİŞ FONKSİYON: edit_payment
@app.route('/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_payment(payment_id):
    conn = get_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            next_url = safe_next(request.form.get('next') or request.args.get('next'))
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
