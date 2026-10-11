"""Screens that look forward or at what is late: cash flow forecast, check
calendar, overdue report and project profitability.

Every route here only reads. They are GET routes behind login_required."""
from flask import render_template, request, flash, session
from db import get_connection
from core import app
from helpers import login_required, today_istanbul
from cashflow import (
    ZERO, PERIOD_CHOICES, horizon, load_expected_flows, build_cashflow,
    parse_money_arg,
)
from check_calendar import load_calendar_checks, group_checks
from overdue import BUCKETS, load_overdue, bucket_totals


@app.route('/cashflow')
@login_required
def cashflow():
    months = request.args.get('months', type=int)
    if months not in PERIOD_CHOICES:
        months = PERIOD_CHOICES[0]
    project_id = request.args.get('project_id', type=int)

    # The opening balance is only typed into the address bar or the form. It
    # is never stored anywhere.
    opening_text = (request.args.get('opening') or '').strip()
    try:
        opening = parse_money_arg(opening_text)
    except ValueError:
        flash('Başlangıç bakiyesi geçerli bir sayı değil, 0 kabul edildi.',
              'warning')
        opening, opening_text = ZERO, ''

    today = today_istanbul()
    start_monday, end_exclusive = horizon(today, months)

    projects, flows = [], []
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        projects = cur.fetchall()
        flows = load_expected_flows(cur, end_exclusive, project_id)
    except Exception:
        app.logger.exception('Failed to build the cash flow forecast')
        flash('Nakit akışı hesaplanırken bir hata oluştu. Liste eksik olabilir.',
              'danger')
    finally:
        cur.close()
        conn.close()

    result = build_cashflow(flows, start_monday, end_exclusive, opening)
    chart = {
        'labels': ['Gecikmiş' if w['late'] else w['start'].strftime('%d.%m')
                   for w in result['weeks']],
        'incoming': [float(w['incoming']) for w in result['weeks']],
        'outgoing': [float(w['outgoing']) for w in result['weeks']],
        'cumulative': [float(w['cumulative']) for w in result['weeks']],
    }
    return render_template('cashflow.html',
                           result=result, chart=chart, today=today,
                           months=months, period_choices=PERIOD_CHOICES,
                           projects=projects, selected_project_id=project_id,
                           opening_text=opening_text,
                           user_name=session.get('user_name'))


@app.route('/check-calendar')
@login_required
def check_calendar():
    today = today_istanbul()
    checks = []
    conn = get_connection()
    cur = conn.cursor()
    try:
        checks = load_calendar_checks(cur)
    except Exception:
        app.logger.exception('Failed to build the check calendar')
        flash('Çek takvimi hazırlanırken bir hata oluştu. Liste eksik olabilir.',
              'danger')
    finally:
        cur.close()
        conn.close()

    grouped = group_checks(checks, today)
    return render_template('check_calendar.html', grouped=grouped, today=today,
                           user_name=session.get('user_name'))


@app.route('/overdue')
@login_required
def overdue_report():
    today = today_istanbul()
    installments, checks = [], []
    conn = get_connection()
    cur = conn.cursor()
    try:
        installments, checks = load_overdue(cur, today)
    except Exception:
        app.logger.exception('Failed to build the overdue report')
        flash('Gecikme raporu hazırlanırken bir hata oluştu. Liste eksik '
              'olabilir.', 'danger')
    finally:
        cur.close()
        conn.close()

    return render_template('overdue.html', today=today, buckets=BUCKETS,
                           installments=installments,
                           installment_totals=bucket_totals(installments),
                           checks=checks, check_totals=bucket_totals(checks),
                           user_name=session.get('user_name'))
