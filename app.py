from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector, bcrypt, re, uuid, os
from functools import wraps
from datetime import datetime, date, timedelta

app = Flask(__name__)
app.secret_key = 'tickit_secret_key_2024'

# ── MYSQL CONFIG ──────────────────────────────────────────────
DB_CONFIG = {
    'host':     'localhost',
    'user':     'root',
    'password': '1234',          # ← put your MySQL root password here if you have one
    'database': 'tickit_db',
    'charset':  'utf8mb4',
}

# ── DB CONNECTION ─────────────────────────────────────────────
def get_db():
    db = mysql.connector.connect(**DB_CONFIG)
    return db

def query(db, sql, params=(), one=False):
    """Execute a SELECT and return dict rows."""
    cur = db.cursor(dictionary=True)
    cur.execute(sql, params)
    return cur.fetchone() if one else cur.fetchall()

def execute(db, sql, params=()):
    """Execute INSERT / UPDATE / DELETE."""
    cur = db.cursor()
    cur.execute(sql, params)
    return cur.lastrowid

def executemany(db, sql, params_list):
    cur = db.cursor()
    cur.executemany(sql, params_list)

# ── HELPERS ───────────────────────────────────────────────────
def is_valid_email(v):  return re.match(r'^[\w\.-]+@[\w\.-]+\.\w{2,}$', v)
def is_valid_phone(v):  return re.match(r'^(\+63|0)\d{10}$', v)

TICKET_PRICES = {'Regular': 450, 'Student': 350, 'Senior / PWD': 360}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        try:
            db  = get_db()
            exists = query(db, "SELECT id FROM users WHERE id=%s", (session['user_id'],), one=True)
            db.close()
            if not exists:
                session.clear()
                flash('Your session has expired. Please log in again.', 'warning')
                return redirect(url_for('login'))
        except Exception:
            pass
        return f(*args, **kwargs)
    return decorated

# ── DB MAINTENANCE ────────────────────────────────────────────
def run_maintenance(db):
    """Auto-complete past showings, release expired locks."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(db, """
        UPDATE showings
           SET status = 'completed'
         WHERE status IN ('open','scheduled','full')
           AND CONCAT(show_date, ' ', show_time) < %s
    """, (now,))
    execute(db, """
        UPDATE seats SET status='available', locked_until=NULL
         WHERE status='locked' AND locked_until < %s
    """, (now,))
    execute(db, """
        UPDATE bookings SET status='Completed'
         WHERE status='Confirmed'
           AND showing_id IN (SELECT id FROM showings WHERE status='completed')
    """)
    db.commit()

# ── SEED SEATS ────────────────────────────────────────────────
def seed_seats(db, showing_id):
    """Seed 50 seats (rows A-E, 10 seats each). A & B = VIP."""
    rows_config = [
        ('A', 'VIP'), ('B', 'VIP'),
        ('C', 'Standard'), ('D', 'Standard'), ('E', 'Standard'),
    ]
    for row_label, category in rows_config:
        for num in range(1, 11):
            seat_code = f"{row_label}{num}"
            execute(db, """
                INSERT IGNORE INTO seats
                    (showing_id, row_label, seat_number, seat_code, category, status)
                VALUES (%s, %s, %s, %s, %s, 'available')
            """, (showing_id, row_label, num, seat_code, category))
    db.commit()

def ensure_seats(db, showing_id):
    row = query(db, "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s", (showing_id,), one=True)
    if row['cnt'] == 0:
        seed_seats(db, showing_id)

# ── GENERATE FUTURE SHOWINGS ──────────────────────────────────
def ensure_future_showings(db, movie_id, cinema_id, days_ahead=3):
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=days_ahead)).isoformat()
    row = query(db, """
        SELECT COUNT(*) AS cnt FROM showings
         WHERE movie_id=%s AND cinema_id=%s
           AND show_date > %s AND show_date <= %s
           AND status IN ('open','scheduled')
    """, (movie_id, cinema_id, today, limit), one=True)

    if row['cnt'] < 2:
        timeslots = ['10:00:00', '13:30:00', '16:30:00', '19:30:00', '22:00:00']
        for d in range(0, days_ahead + 1):
            show_date = (date.today() + timedelta(days=d)).isoformat()
            for t in timeslots:
                execute(db, """
                    INSERT IGNORE INTO showings
                        (movie_id, cinema_id, show_date, show_time, status)
                    VALUES (%s, %s, %s, %s, 'open')
                """, (movie_id, cinema_id, show_date, t))
        db.commit()

# ── TIME HELPER ────────────────────────────────────────────────
def _fmt_time(t):
    """Format a TIME / HH:MM:SS to 12-hour AM/PM."""
    if not t:
        return ''
    # MySQL TIME can come as timedelta
    if isinstance(t, timedelta):
        total = int(t.total_seconds())
        hrs, rem = divmod(total, 3600)
        mins = rem // 60
    else:
        parts = str(t).split(':')
        hrs  = int(parts[0])
        mins = int(parts[1]) if len(parts) > 1 else 0
    suffix = 'AM' if hrs < 12 else 'PM'
    return f'{hrs % 12 or 12}:{mins:02d} {suffix}'

# ── ROUTES ────────────────────────────────────────────────────
@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('landing.html')

@app.route('/home')
@login_required
def index():
    return render_template('index.html', user_name=session.get('user_name'))

@app.route('/movies')
@login_required
def movies():
    return render_template('movies.html', user_name=session.get('user_name'))

# ── BOOKING ───────────────────────────────────────────────────
@app.route('/booking')
@login_required
def booking():
    db = get_db()
    run_maintenance(db)

    movie_id   = request.args.get('movie_id',   type=int)
    showing_id = request.args.get('showing_id', type=int)

    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=3)).isoformat()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── All active movies ─────────────────────────────────────
    all_movies_raw = query(db, """
        SELECT m.id, m.title, m.genre, m.rating, m.poster_path, m.duration_mins,
               (SELECT MIN(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status IN ('open','scheduled')
                   AND CONCAT(s.show_date, ' ', s.show_time) > %s
               ) AS next_date,
               (SELECT COUNT(*) FROM showings s
                 WHERE s.movie_id=m.id AND s.show_date=%s
                   AND s.status IN ('open','full')) AS today_count,
               (SELECT MAX(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status='completed') AS last_played
        FROM movies m WHERE m.status='active'
        ORDER BY today_count DESC, next_date ASC
    """, (now, today))

    # Convert date objects for Jinja
    all_movies = []
    for r in all_movies_raw:
        m = dict(r)
        if m['next_date'] and not isinstance(m['next_date'], str):
            m['next_date'] = m['next_date']   # keep as date object for .strftime
        all_movies.append(m)

    selected_movie   = None
    showings_by_date = {}
    selected_showing = None
    seat_rows        = []

    # ── Step 2: Showings for selected movie ───────────────────
    if movie_id:
        selected_movie = query(db,
            "SELECT * FROM movies WHERE id=%s AND status='active'",
            (movie_id,), one=True)

        if selected_movie:
            # Auto-generate showings for every cinema
            cinemas_all = query(db, "SELECT id FROM cinemas")
            for _c in cinemas_all:
                ensure_future_showings(db, movie_id, _c['id'], days_ahead=3)

            raw_showings = query(db, """
                SELECT s.id, s.show_date, s.show_time, s.status, s.total_seats,
                       c.name AS cinema_name, c.location AS cinema_location,
                       COALESCE(
                           (SELECT COUNT(*) FROM seats st
                            WHERE st.showing_id=s.id AND st.status='booked'), 0
                       ) AS booked_count,
                       COALESCE(
                           (SELECT COUNT(*) FROM seats st
                            WHERE st.showing_id=s.id AND st.status='available'), 0
                       ) AS avail_count,
                       COALESCE(
                           (SELECT COUNT(*) FROM seats st
                            WHERE st.showing_id=s.id), 0
                       ) AS total_seeded
                FROM showings s
                JOIN cinemas c ON c.id = s.cinema_id
                WHERE s.movie_id=%s
                  AND s.status IN ('open','scheduled','full')
                  AND CONCAT(s.show_date, ' ', s.show_time) > %s
                  AND s.show_date <= %s
                ORDER BY s.show_date, s.show_time
            """, (movie_id, now, limit))

            for sh in raw_showings:
                sh = dict(sh)
                if sh['total_seeded'] == 0:
                    ensure_seats(db, sh['id'])
                    sh['avail_count'] = 50

                if sh['avail_count'] == 0 and sh['booked_count'] == 0:
                    sh['avail_count'] = sh['total_seats']

                # show_date from MySQL is a date object
                d_obj   = sh['show_date']
                d_str   = d_obj.isoformat() if hasattr(d_obj, 'isoformat') else str(d_obj)
                d_label = d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else d_str

                if d_str not in showings_by_date:
                    showings_by_date[d_str] = {'label': d_label, 'showings': []}

                avail = sh['avail_count']
                if avail == 0:
                    sh['avail_label'] = 'SOLD OUT'
                    sh['avail_class'] = 'full'
                elif avail <= 8:
                    sh['avail_label'] = f'Only {avail} left!'
                    sh['avail_class'] = 'low'
                else:
                    sh['avail_label'] = f'{avail} of {sh["total_seats"]} available'
                    sh['avail_class'] = 'ok'

                sh['show_time_fmt'] = _fmt_time(sh['show_time'])
                showings_by_date[d_str]['showings'].append(sh)

    # ── Step 3: Seat map for selected showing ─────────────────
    if showing_id:
        ensure_seats(db, showing_id)

        row = query(db, """
            SELECT s.id, s.show_date, s.show_time, s.status AS show_status,
                   s.total_seats,
                   c.name AS cinema_name, c.location AS cinema_location,
                   m.title AS movie_title, m.genre, m.rating, m.poster_path,
                   m.id AS movie_id_val
            FROM showings s
            JOIN cinemas c ON c.id=s.cinema_id
            JOIN movies  m ON m.id=s.movie_id
            WHERE s.id=%s
        """, (showing_id,), one=True)

        if row:
            selected_showing = dict(row)
            selected_showing['show_time_fmt'] = _fmt_time(selected_showing['show_time'])
            d_obj = selected_showing['show_date']
            selected_showing['show_date_fmt'] = d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj)

            if not movie_id:
                movie_id = selected_showing['movie_id_val']
            if not selected_movie:
                selected_movie = {
                    'id':          selected_showing['movie_id_val'],
                    'title':       selected_showing['movie_title'],
                    'genre':       selected_showing['genre'],
                    'rating':      selected_showing['rating'],
                    'poster_path': selected_showing['poster_path'],
                }

        all_seats_raw = query(db, """
            SELECT st.id, st.row_label, st.seat_number, st.seat_code,
                   st.category, st.status, st.locked_until
            FROM seats st
            WHERE st.showing_id=%s
            ORDER BY st.row_label, st.seat_number
        """, (showing_id,))

        from collections import defaultdict
        rows_dict = defaultdict(list)
        for s in all_seats_raw:
            rows_dict[s['row_label']].append(dict(s))
        seat_rows = [{'label': k, 'seats': v, 'category': v[0]['category']}
                     for k, v in sorted(rows_dict.items())]

    db.close()
    return render_template('booking.html',
        user_name        = session.get('user_name'),
        all_movies       = all_movies,
        selected_movie   = selected_movie,
        movie_id         = movie_id,
        showings_by_date = showings_by_date,
        selected_showing = selected_showing,
        showing_id       = showing_id,
        seat_rows        = seat_rows,
        booking_success  = False,
        errors={}, form={}
    )


# ── API: LOCK SEAT ─────────────────────────────────────────────
@app.route('/api/lock-seat', methods=['POST'])
@login_required
def lock_seat():
    data       = request.get_json(force=True)
    seat_id    = data.get('seat_id')
    showing_id = data.get('showing_id')
    if not seat_id or not showing_id:
        return jsonify({'ok': False, 'msg': 'Missing params'})

    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        execute(db, """
            UPDATE seats SET status='available', locked_until=NULL
             WHERE showing_id=%s AND status='locked' AND locked_until < %s
        """, (showing_id, now))

        seat = query(db, "SELECT * FROM seats WHERE id=%s", (seat_id,), one=True)
        if not seat or seat['status'] != 'available':
            db.close()
            return jsonify({'ok': False, 'msg': 'Seat no longer available'})

        lock_exp = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        execute(db, "UPDATE seats SET status='locked', locked_until=%s WHERE id=%s",
                (lock_exp, seat_id))
        db.commit()
        db.close()
        return jsonify({'ok': True, 'expires': lock_exp[-8:]})
    except Exception as e:
        db.close()
        return jsonify({'ok': False, 'msg': str(e)})


# ── API: UNLOCK SEAT ───────────────────────────────────────────
@app.route('/api/unlock-seat', methods=['POST'])
@login_required
def unlock_seat():
    data    = request.get_json(force=True)
    seat_id = data.get('seat_id')
    if not seat_id:
        return jsonify({'ok': False})
    db = get_db()
    execute(db, "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s AND status='locked'",
            (seat_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ── API: SEAT STATUS ───────────────────────────────────────────
@app.route('/api/seat-status/<int:showing_id>')
@login_required
def seat_status(showing_id):
    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(db, """
        UPDATE seats SET status='available', locked_until=NULL
         WHERE showing_id=%s AND status='locked' AND locked_until < %s
    """, (showing_id, now))
    db.commit()
    seats = query(db, """
        SELECT id, seat_code, status, category, row_label, seat_number
        FROM seats WHERE showing_id=%s ORDER BY row_label, seat_number
    """, (showing_id,))
    db.close()
    return jsonify({'seats': seats})


# ── CONFIRM BOOKING ────────────────────────────────────────────
@app.route('/booking/confirm', methods=['POST'])
@login_required
def confirm_booking():
    seat_ids_raw  = request.form.get('seat_ids', '').strip()
    showing_id    = request.form.get('showing_id', type=int)
    ticket_type   = request.form.get('ticket_type', 'Regular')
    customer_name = request.form.get('customer_name', '').strip()
    contact       = request.form.get('contact', '').strip()
    special       = request.form.get('special_requests', '').strip()

    errors = {}
    if not seat_ids_raw:        errors['seats']         = 'Please select at least one seat.'
    if not showing_id:          errors['showing']        = 'Invalid showing.'
    if not customer_name or len(customer_name) < 2:
                                errors['customer_name']  = 'Valid name required (min 2 chars).'
    if not contact or not re.match(r'^(\+63|0)\d{10}$', contact):
                                errors['contact']        = 'Enter a valid PH mobile (09XXXXXXXXX).'
    if ticket_type not in TICKET_PRICES:
                                errors['ticket_type']    = 'Invalid ticket type.'

    seat_ids = [int(x) for x in seat_ids_raw.split(',') if x.strip().isdigit()]
    if not seat_ids:            errors['seats'] = 'No valid seats selected.'
    elif len(seat_ids) > 10:    errors['seats'] = 'Maximum 10 seats per booking.'

    if errors:
        flash(' | '.join(errors.values()), 'error')
        return redirect(url_for('booking', showing_id=showing_id))

    db = get_db()
    try:
        if not query(db, "SELECT id FROM users WHERE id=%s", (session['user_id'],), one=True):
            db.close()
            session.clear()
            flash('Session expired. Please log in again.', 'warning')
            return redirect(url_for('login'))

        showing = query(db, "SELECT * FROM showings WHERE id=%s", (showing_id,), one=True)
        if not showing or showing['status'] not in ('open', 'scheduled', 'full'):
            flash('This showing is no longer available.', 'error')
            db.close()
            return redirect(url_for('booking'))

        for sid in seat_ids:
            seat = query(db, "SELECT * FROM seats WHERE id=%s", (sid,), one=True)
            if not seat or seat['status'] == 'booked':
                code = seat['seat_code'] if seat else str(sid)
                flash(f'Seat {code} was just taken. Please re-select.', 'error')
                db.close()
                return redirect(url_for('booking', showing_id=showing_id))

        unit_price  = TICKET_PRICES[ticket_type]
        total_price = unit_price * len(seat_ids)
        ref_code    = 'TKT-' + uuid.uuid4().hex[:8].upper()

        # Build seat codes string
        placeholders = ','.join(['%s'] * len(seat_ids))
        seat_info = query(db,
            f"SELECT seat_code, category FROM seats WHERE id IN ({placeholders})",
            seat_ids)
        seat_codes_str = ', '.join(f"{s['seat_code']} ({s['category']})" for s in seat_info)

        for sid in seat_ids:
            execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s", (sid,))
            execute(db, """
                INSERT INTO bookings
                    (user_id, showing_id, seat_id, booking_ref, ref_code,
                     ticket_type, ticket_count, unit_price, total_price,
                     seat_codes, customer_name, contact, special_requests)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (session['user_id'], showing_id, sid, ref_code, ref_code,
                  ticket_type, len(seat_ids), unit_price, total_price,
                  seat_codes_str, customer_name, contact, special))

        avail = query(db,
            "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s AND status='available'",
            (showing_id,), one=True)['cnt']
        if avail == 0:
            execute(db, "UPDATE showings SET status='full' WHERE id=%s", (showing_id,))

        db.commit()

        sh = query(db, """
            SELECT s.show_date, s.show_time, c.name AS cinema, m.title AS movie
            FROM showings s
            JOIN cinemas c ON c.id=s.cinema_id
            JOIN movies  m ON m.id=s.movie_id
            WHERE s.id=%s
        """, (showing_id,), one=True)

        d_obj = sh['show_date']
        date_fmt = d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj)

        booking_data = {
            'movie':        sh['movie'],
            'cinema':       sh['cinema'],
            'date':         date_fmt,
            'showtime':     _fmt_time(sh['show_time']),
            'seats':        seat_codes_str,
            'ticket_count': len(seat_ids),
            'ticket_type':  ticket_type,
            'total_price':  f'{total_price:,}',
            'ref':          ref_code,
        }
        db.close()

        return render_template('booking.html',
            user_name        = session.get('user_name'),
            booking_success  = True,
            booking          = booking_data,
            all_movies=[], selected_movie=None, movie_id=None,
            showings_by_date={}, selected_showing=None, showing_id=None,
            seat_rows=[], errors={}, form={}
        )

    except Exception as e:
        db.close()
        flash(f'Booking error: {str(e)}', 'error')
        return redirect(url_for('booking', showing_id=showing_id))


# ── MY BOOKINGS ────────────────────────────────────────────────
@app.route('/my-bookings')
@login_required
def my_bookings():
    db  = get_db()
    rows = query(db, """
        SELECT b.ref_code, b.ticket_type, b.unit_price, b.status AS booking_status,
               b.created_at, b.customer_name, b.contact,
               st.seat_code, st.category,
               m.title AS movie, c.name AS cinema,
               s.show_date, s.show_time
        FROM bookings b
        JOIN seats    st ON st.id  = b.seat_id
        JOIN showings s  ON s.id   = b.showing_id
        JOIN movies   m  ON m.id   = s.movie_id
        JOIN cinemas  c  ON c.id   = s.cinema_id
        WHERE b.user_id = %s
        ORDER BY b.created_at DESC
    """, (session['user_id'],))
    db.close()

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['ref_code']].append(dict(r))

    bookings_list = []
    for ref, seats in grouped.items():
        first = seats[0]
        total = sum(s['unit_price'] for s in seats)
        d_obj = first['show_date']
        date_fmt = d_obj.strftime('%b %d, %Y') if hasattr(d_obj, 'strftime') else str(d_obj)
        bookings_list.append({
            'ref':         ref,
            'movie':       first['movie'],
            'cinema':      first['cinema'],
            'date':        date_fmt,
            'showtime':    _fmt_time(first['show_time']),
            'seats':       ', '.join(s['seat_code'] for s in seats),
            'ticket_type': first['ticket_type'],
            'total':       total,
            'status':      first['booking_status'],
            'booked_on':   first['created_at'],
        })

    return render_template('my_bookings.html',
        user_name=session.get('user_name'),
        bookings=bookings_list
    )


# ── LOGIN ──────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    errors = {}; form = {}
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password',   '').strip()
        form = {'identifier': identifier}
        if not identifier:
            errors['identifier'] = 'Email or mobile is required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
            errors['identifier'] = 'Enter a valid email or PH mobile (09XXXXXXXXX).'
        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 6:
            errors['password'] = 'Min 6 characters.'
        if not errors:
            # ── Admin shortcut ─────────────────────────────────
            if identifier == 'admin@gmail.com' and password == ADMIN_PASSWORD:
                session['is_admin']   = True
                session['admin_name'] = 'Admin'
                return redirect(url_for('admin_dashboard'))
            try:
                db   = get_db()
                user = query(db,
                    'SELECT * FROM users WHERE email=%s OR mobile=%s',
                    (identifier, identifier), one=True)
                db.close()
                if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
                    session['user_id']   = user['id']
                    session['user_name'] = user['full_name']
                    return redirect(url_for('index'))
                else:
                    errors['general'] = 'Invalid credentials. Please try again.'
            except Exception as e:
                errors['general'] = f'Database error: {e}'
    return render_template('login.html', errors=errors, form=form)


# ── REGISTER ───────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    errors = {}; form = {}
    if request.method == 'POST':
        identifier = request.form.get('identifier',       '').strip()
        full_name  = request.form.get('full_name',        '').strip()
        age        = request.form.get('age',              '').strip()
        gender     = request.form.get('gender',           '').strip()
        province   = request.form.get('province',         '').strip()
        city       = request.form.get('city',             '').strip()
        barangay   = request.form.get('barangay',         '').strip()
        password   = request.form.get('password',         '').strip()
        confirm_pw = request.form.get('confirm_password', '').strip()
        form = dict(identifier=identifier, full_name=full_name, age=age,
                    gender=gender, province=province, city=city, barangay=barangay)

        if not identifier:                                errors['identifier']       = 'Required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
                                                          errors['identifier']       = 'Enter valid email or 09XXXXXXXXX.'
        if not full_name:                                 errors['full_name']        = 'Required.'
        elif len(full_name) < 2:                          errors['full_name']        = 'Min 2 chars.'
        if not age:                                       errors['age']              = 'Required.'
        elif not age.isdigit() or not (1 <= int(age) <= 120):
                                                          errors['age']              = 'Enter valid age (1-120).'
        if not gender:                                    errors['gender']           = 'Select gender.'
        if not province:                                  errors['province']         = 'Select province.'
        if not city:                                      errors['city']             = 'Select city.'
        if not barangay:                                  errors['barangay']         = 'Select barangay.'
        if not password:                                  errors['password']         = 'Required.'
        elif len(password) < 6:                           errors['password']         = 'Min 6 chars.'
        elif not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
                                                          errors['password']         = 'Must contain letters and numbers.'
        if not confirm_pw:                                errors['confirm_password'] = 'Confirm your password.'
        elif password != confirm_pw:                      errors['confirm_password'] = 'Passwords do not match.'

        if not errors:
            try:
                db     = get_db()
                email  = identifier if is_valid_email(identifier) else None
                mobile = identifier if is_valid_phone(identifier) else None
                exists = query(db,
                    'SELECT id FROM users WHERE email=%s OR mobile=%s',
                    (email, mobile), one=True)
                if exists:
                    errors['identifier'] = 'Already registered. Please log in.'
                else:
                    hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    address = f"{barangay}, {city}, {province}"
                    execute(db, """
                        INSERT INTO users (email, mobile, full_name, age, gender, address, password)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (email, mobile, full_name, int(age), gender, address, hashed))
                    db.commit()
                    db.close()
                    flash(f'Welcome, {full_name}! Your account is ready.', 'success')
                    return redirect(url_for('login'))
                db.close()
            except Exception as e:
                errors['general'] = f'Database error: {e}'

    return render_template('register.html', errors=errors, form=form)


# ── LOGOUT ─────────────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing'))


# ═══════════════════════════════════════════════════════════════
#  ADMIN SECTION
# ═══════════════════════════════════════════════════════════════
ADMIN_PASSWORD = 'admin12345'  # used by admin@gmail.com login

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # Admin logs in via the main /login page using admin@gmail.com
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_name', None)
    flash('Logged out of admin panel.', 'info')
    return redirect(url_for('admin_login'))

# ── Admin Dashboard ─────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {}
    stats['available_seats']    = query(db, "SELECT COUNT(*) AS n FROM seats WHERE status='available'",            one=True)['n']
    stats['total_sales']        = query(db, "SELECT COALESCE(SUM(total_price),0) AS n FROM bookings WHERE status IN ('Confirmed','Completed')", one=True)['n']
    stats['total_bookings']     = query(db, "SELECT COUNT(*) AS n FROM bookings",                                  one=True)['n']
    stats['confirmed_bookings'] = query(db, "SELECT COUNT(*) AS n FROM bookings WHERE status='Confirmed'",         one=True)['n']
    stats['active_movies']      = query(db, "SELECT COUNT(*) AS n FROM movies WHERE status='active'",              one=True)['n']
    stats['total_movies']       = query(db, "SELECT COUNT(*) AS n FROM movies",                                    one=True)['n']
    stats['total_users']        = query(db, "SELECT COUNT(*) AS n FROM users",                                     one=True)['n']
    stats['today_showings']     = query(db, "SELECT COUNT(*) AS n FROM showings WHERE show_date=%s AND status IN ('open','full')",
                                        (date.today().isoformat(),), one=True)['n']

    recent_bookings = query(db, """
        SELECT b.id, b.booking_ref, b.customer_name, b.total_price, b.status,
               b.ticket_count, b.ticket_type, b.seat_codes,
               m.title AS movie_title, s.show_date, s.show_time
        FROM bookings b
        JOIN showings s ON b.showing_id=s.id
        JOIN movies   m ON s.movie_id=m.id
        ORDER BY b.id DESC LIMIT 10
    """)

    active_movies = query(db, """
        SELECT m.*,
               COALESCE((SELECT COUNT(*) FROM showings sh
                          JOIN seats st ON st.showing_id=sh.id
                         WHERE sh.movie_id=m.id AND st.status='available'), 0) AS avail_seats
        FROM movies m WHERE m.status='active' ORDER BY m.title
    """)
    db.close()
    return render_template('admin_dashboard.html',
                           stats=stats,
                           recent_bookings=recent_bookings,
                           active_movies=active_movies)

# ── Admin Movies ────────────────────────────────────────────────
@app.route('/admin/movies')
@admin_required
def admin_movies():
    db = get_db()
    movies_list = query(db, """
        SELECT m.*,
               COALESCE((SELECT COUNT(*) FROM showings sh
                          JOIN seats st ON st.showing_id=sh.id
                         WHERE sh.movie_id=m.id AND st.status='available'), 0) AS avail_seats
        FROM movies m ORDER BY m.title
    """)
    db.close()
    return render_template('admin_movies.html', movies=movies_list)

@app.route('/admin/movies/add', methods=['POST'])
@admin_required
def admin_movies_add():
    title        = request.form.get('title', '').strip()
    genre        = request.form.get('genre', '').strip()
    cast_members = request.form.get('cast_members', '').strip()
    duration     = request.form.get('duration_mins', '').strip()
    rating       = request.form.get('rating', '0').strip() or '0'
    release_date = request.form.get('release_date', '').strip() or None
    status       = request.form.get('status', 'active')
    description  = request.form.get('description', '').strip()

    if not title or not genre or not duration:
        flash('Title, genre, and duration are required.', 'error')
        return redirect(url_for('admin_movies'))

    poster_path = 'images/no_poster.png'
    poster_file = request.files.get('poster')
    if poster_file and poster_file.filename:
        from werkzeug.utils import secure_filename
        filename = secure_filename(poster_file.filename)
        save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
        os.makedirs(save_dir, exist_ok=True)
        poster_file.save(os.path.join(save_dir, filename))
        poster_path = f'images/movies/{filename}'

    try:
        db = get_db()
        execute(db, """
            INSERT INTO movies (title, genre, cast_members, duration_mins, rating,
                                release_date, status, description, poster_path)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (title, genre, cast_members, int(duration), float(rating),
              release_date, status, description, poster_path))
        db.commit()
        db.close()
        flash(f'Movie "{title}" added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding movie: {e}', 'error')
    return redirect(url_for('admin_movies'))

@app.route('/admin/movies/edit/<int:movie_id>', methods=['POST'])
@admin_required
def admin_movies_edit(movie_id):
    title        = request.form.get('title', '').strip()
    genre        = request.form.get('genre', '').strip()
    cast_members = request.form.get('cast_members', '').strip()
    duration     = request.form.get('duration_mins', '').strip()
    rating       = request.form.get('rating', '0').strip() or '0'
    release_date = request.form.get('release_date', '').strip() or None
    status       = request.form.get('status', 'active')
    description  = request.form.get('description', '').strip()

    if not title or not genre or not duration:
        flash('Title, genre, and duration are required.', 'error')
        return redirect(url_for('admin_movies'))

    try:
        db = get_db()
        poster_file = request.files.get('poster')
        if poster_file and poster_file.filename:
            from werkzeug.utils import secure_filename
            filename = secure_filename(poster_file.filename)
            save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
            os.makedirs(save_dir, exist_ok=True)
            poster_file.save(os.path.join(save_dir, filename))
            execute(db, """
                UPDATE movies SET title=%s, genre=%s, cast_members=%s, duration_mins=%s,
                                  rating=%s, release_date=%s, status=%s, description=%s, poster_path=%s
                WHERE id=%s
            """, (title, genre, cast_members, int(duration), float(rating),
                  release_date, status, description, f'images/movies/{filename}', movie_id))
        else:
            execute(db, """
                UPDATE movies SET title=%s, genre=%s, cast_members=%s, duration_mins=%s,
                                  rating=%s, release_date=%s, status=%s, description=%s
                WHERE id=%s
            """, (title, genre, cast_members, int(duration), float(rating),
                  release_date, status, description, movie_id))
        db.commit()
        db.close()
        flash(f'Movie "{title}" updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating movie: {e}', 'error')
    return redirect(url_for('admin_movies'))

@app.route('/admin/movies/delete', methods=['POST'])
@admin_required
def admin_movies_delete():
    movie_id = request.form.get('movie_id', type=int)
    if not movie_id:
        flash('Invalid movie.', 'error')
        return redirect(url_for('admin_movies'))
    try:
        db = get_db()
        movie = query(db, "SELECT title FROM movies WHERE id=%s", (movie_id,), one=True)
        if movie:
            execute(db, "DELETE FROM seats WHERE showing_id IN (SELECT id FROM showings WHERE movie_id=%s)", (movie_id,))
            execute(db, "DELETE FROM bookings WHERE showing_id IN (SELECT id FROM showings WHERE movie_id=%s)", (movie_id,))
            execute(db, "DELETE FROM showings WHERE movie_id=%s", (movie_id,))
            execute(db, "DELETE FROM movies WHERE id=%s", (movie_id,))
            db.commit()
            flash(f'Movie "{movie["title"]}" deleted.', 'success')
        db.close()
    except Exception as e:
        flash(f'Error deleting movie: {e}', 'error')
    return redirect(url_for('admin_movies'))

# ── Admin Bookings ──────────────────────────────────────────────
@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    db = get_db()
    bookings_list = query(db, """
        SELECT b.id, b.booking_ref, b.customer_name, b.contact, b.total_price,
               b.status, b.ticket_count, b.ticket_type, b.seat_codes,
               m.title AS movie_title, c.name AS cinema_name,
               s.show_date, s.show_time
        FROM bookings b
        JOIN showings s ON b.showing_id=s.id
        JOIN movies   m ON s.movie_id=m.id
        JOIN cinemas  c ON s.cinema_id=c.id
        ORDER BY b.id DESC
    """)
    db.close()
    return render_template('admin_bookings.html', bookings=bookings_list)

@app.route('/admin/bookings/cancel', methods=['POST'])
@admin_required
def admin_bookings_cancel():
    booking_id = request.form.get('booking_id', type=int)
    if not booking_id:
        flash('Invalid booking.', 'error')
        return redirect(url_for('admin_bookings'))
    try:
        db = get_db()
        execute(db, "UPDATE bookings SET status='Cancelled' WHERE id=%s", (booking_id,))
        # Free the seat
        bk = query(db, "SELECT seat_id FROM bookings WHERE id=%s", (booking_id,), one=True)
        if bk:
            execute(db, "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s", (bk['seat_id'],))
        db.commit()
        db.close()
        flash('Booking cancelled successfully.', 'success')
    except Exception as e:
        flash(f'Error cancelling booking: {e}', 'error')
    return redirect(url_for('admin_bookings'))

# ── Admin Users ─────────────────────────────────────────────────
@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db()
    users_list = query(db, """
        SELECT u.*,
               COALESCE((SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id), 0) AS booking_count
        FROM users u ORDER BY u.id DESC
    """)
    db.close()
    return render_template('admin_users.html', users=users_list)

@app.route('/admin/users/delete', methods=['POST'])
@admin_required
def admin_users_delete():
    user_id = request.form.get('user_id', type=int)
    if not user_id:
        flash('Invalid user.', 'error')
        return redirect(url_for('admin_users'))
    try:
        db = get_db()
        user = query(db, "SELECT full_name FROM users WHERE id=%s", (user_id,), one=True)
        if user:
            execute(db, "DELETE FROM bookings WHERE user_id=%s", (user_id,))
            execute(db, "DELETE FROM users WHERE id=%s", (user_id,))
            db.commit()
            flash(f'User "{user["full_name"]}" deleted.', 'success')
        db.close()
    except Exception as e:
        flash(f'Error deleting user: {e}', 'error')
    return redirect(url_for('admin_users'))


# ── STARTUP ────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
