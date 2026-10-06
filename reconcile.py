"""Spread the payments of a flat or an expense over its installments.

Money code: these functions were moved here unchanged from app.py. Do not
edit them without tests (tests/test_reconcile_*.py)."""


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
