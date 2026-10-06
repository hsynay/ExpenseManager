"""Login, logout, the health check and the start page."""
from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from db import get_connection
from werkzeug.security import check_password_hash
from core import app, limiter


def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, password_hash, full_name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return user 


@app.route("/ping")
def ping():
    return jsonify({"status": "ok"}), 200

@app.route('/login', methods=['GET', 'POST'])
# Slow down automated password guessing without blocking real people: two
# users share one account from the same office, so the limit is generous.
# Only POST is limited, and deduct_when counts the attempt only when the page
# is rendered again (status 200 = wrong password). A successful login answers
# with a redirect (302) and is never counted.
@limiter.limit(
    '10 per minute',
    methods=['POST'],
    deduct_when=lambda response: response.status_code == 200,
)
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = get_user_by_email(email)
        if user and check_password_hash(user[2], password):
            session['user_id'] = user[0]
            session['user_name'] = user[3]
            # Needed for PERMANENT_SESSION_LIFETIME to apply. Flask refreshes
            # the cookie on each request, so the session ends 12 hours after
            # the last activity.
            session.permanent = True
            return redirect(url_for('dashboard'))
        else:
            flash('E-posta veya şifre hatalı.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    return redirect(url_for('dashboard'))
