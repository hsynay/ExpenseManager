from datetime import date, timedelta
from db import get_connection

def create_installment_schedule(flat_id, total_price, total_installments, start_date=None):
    if not start_date:
        start_date = date.today()

    monthly_amount = total_price / total_installments

    conn = get_connection()
    cur = conn.cursor()

    for i in range(total_installments):
        due_date = start_date + timedelta(days=30*i)
        cur.execute("""
            INSERT INTO installment_schedule (flat_id, due_date, amount)
            VALUES (%s, %s, %s)
        """, (flat_id, due_date, monthly_amount))

    conn.commit()
    cur.close()
    conn.close()
