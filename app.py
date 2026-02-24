from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import bcrypt
import re
from functools import wraps

app = Flask(__name__)
app.secret_key = 'tickit_secret_key_2024'

# ── DB CONNECTION ──────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host='localhost',
        port=3306,
        user='root',
        password='2006',
        database='tickit_db'
    )

# ── HELPERS ───────────────────────────────────────────────────
def is_valid_email(value):
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w{2,}$', value)

def is_valid_phone(value):
    return re.match(r'^(\+63|0)\d{10}$', value)

# ── LOGIN REQUIRED DECORATOR ──────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first to access that page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── ROUTES ────────────────────────────────────────────────────

# Home — login required
@app.route('/')
@login_required
def index():
    return render_template('index.html', user_name=session.get('user_name'))

# Movies — login required
@app.route('/movies')
@login_required
def movies():
    return render_template('movies.html', user_name=session.get('user_name'))

# Booking — login required
@app.route('/booking')
@login_required
def booking():
    return render_template('movies.html', user_name=session.get('user_name'))

# ── LOGIN ─────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Already logged in — go straight to home
    if 'user_id' in session:
        return redirect(url_for('index'))

    errors = {}
    form   = {}

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password', '').strip()
        form       = {'identifier': identifier}

        if not identifier:
            errors['identifier'] = 'Email or mobile number is required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
            errors['identifier'] = 'Enter a valid email or PH mobile number (e.g. 09XXXXXXXXX).'

        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 6:
            errors['password'] = 'Password must be at least 6 characters.'

        if not errors:
            try:
                db  = get_db()
                cur = db.cursor(dictionary=True)
                cur.execute(
                    'SELECT * FROM users WHERE email=%s OR mobile=%s',
                    (identifier, identifier)
                )
                user = cur.fetchone()
                cur.close()
                db.close()

                if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
                    session['user_id']   = user['id']
                    session['user_name'] = user['full_name']
                    return redirect(url_for('index'))
                else:
                    errors['general'] = 'Invalid email/mobile or password.'
            except Exception as e:
                errors['general'] = f'Database error: {str(e)}'

    return render_template('login.html', errors=errors, form=form)


# ── REGISTER ──────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    # Already logged in
    if 'user_id' in session:
        return redirect(url_for('index'))

    errors = {}
    form   = {}

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        full_name  = request.form.get('full_name', '').strip()
        age        = request.form.get('age', '').strip()
        gender     = request.form.get('gender', '').strip()
        province   = request.form.get('province', '').strip()
        city       = request.form.get('city', '').strip()
        barangay   = request.form.get('barangay', '').strip()
        password   = request.form.get('password', '').strip()
        confirm_pw = request.form.get('confirm_password', '').strip()

        form = {
            'identifier': identifier, 'full_name': full_name,
            'age': age, 'gender': gender,
            'province': province, 'city': city, 'barangay': barangay
        }

        if not identifier:
            errors['identifier'] = 'Email or mobile number is required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
            errors['identifier'] = 'Enter a valid email or PH mobile number (e.g. 09XXXXXXXXX).'

        if not full_name:
            errors['full_name'] = 'Full name is required.'
        elif len(full_name) < 2:
            errors['full_name'] = 'Full name must be at least 2 characters.'

        if not age:
            errors['age'] = 'Age is required.'
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            errors['age'] = 'Enter a valid age (1–120).'

        if not gender:
            errors['gender'] = 'Please select a gender.'

        if not province:
            errors['province'] = 'Please select a province.'
        if not city:
            errors['city'] = 'Please select a city/municipality.'
        if not barangay:
            errors['barangay'] = 'Please select a barangay.'

        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 6:
            errors['password'] = 'Password must be at least 6 characters.'
        elif not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
            errors['password'] = 'Password must contain letters and numbers.'

        if not confirm_pw:
            errors['confirm_password'] = 'Please confirm your password.'
        elif password != confirm_pw:
            errors['confirm_password'] = 'Passwords do not match.'

        if not errors:
            try:
                db  = get_db()
                cur = db.cursor(dictionary=True)

                email  = identifier if is_valid_email(identifier) else None
                mobile = identifier if is_valid_phone(identifier) else None

                cur.execute(
                    'SELECT id FROM users WHERE email=%s OR mobile=%s',
                    (email, mobile)
                )
                if cur.fetchone():
                    errors['identifier'] = 'This email or mobile is already registered.'
                else:
                    hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    address = f"{barangay}, {city}, {province}"
                    cur.execute(
                        '''INSERT INTO users (email, mobile, full_name, age, gender, address, password)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                        (email, mobile, full_name, int(age), gender, address, hashed)
                    )
                    db.commit()
                    cur.close()
                    db.close()
                    flash(f'Account created! Welcome, {full_name}. Please sign in.', 'success')
                    return redirect(url_for('login'))

                cur.close()
                db.close()
            except Exception as e:
                errors['general'] = f'Database error: {str(e)}'

    return render_template('register.html', errors=errors, form=form)


# ── LOGOUT ────────────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)