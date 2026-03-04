from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3, bcrypt, re, uuid, os
from functools import wraps
from datetime import datetime, date, timedelta

app = Flask(__name__)
app.secret_key = 'tickit_secret_key_2024'

DB_PATH = os.path.join(os.path.dirname(__file__), 'tickit.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'tickit_db.sql')

# ── DB INIT ───────────────────────────────────────────────────
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT UNIQUE,
    mobile     TEXT UNIQUE,
    full_name  TEXT NOT NULL,
    age        INTEGER NOT NULL DEFAULT 0,
    gender     TEXT NOT NULL DEFAULT 'Prefer not to say',
    address    TEXT NOT NULL DEFAULT '',
    password   TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS movies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    genre         TEXT NOT NULL,
    rating        REAL NOT NULL DEFAULT 0.0,
    poster_path   TEXT NOT NULL DEFAULT 'images/no_poster.png',
    duration_mins INTEGER NOT NULL DEFAULT 120,
    description   TEXT,
    cast_members  TEXT,
    release_date  TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cinemas (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    location TEXT NOT NULL,
    screens  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS showings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id    INTEGER NOT NULL,
    cinema_id   INTEGER NOT NULL,
    show_date   TEXT NOT NULL,
    show_time   TEXT NOT NULL,
    total_seats INTEGER NOT NULL DEFAULT 50,
    status      TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('scheduled','open','full','completed')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (movie_id)  REFERENCES movies(id)  ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE CASCADE,
    UNIQUE (movie_id, cinema_id, show_date, show_time)
);

CREATE TABLE IF NOT EXISTS seats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    showing_id   INTEGER NOT NULL,
    row_label    TEXT NOT NULL,
    seat_number  INTEGER NOT NULL,
    seat_code    TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'Standard' CHECK(category IN ('VIP','Standard')),
    status       TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','locked','booked')),
    locked_until TEXT NULL,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE,
    UNIQUE (showing_id, seat_code)
);

CREATE TABLE IF NOT EXISTS bookings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    showing_id       INTEGER NOT NULL,
    seat_id          INTEGER,
    booking_ref      TEXT,
    ref_code         TEXT,
    ticket_type      TEXT NOT NULL DEFAULT 'Regular',
    ticket_count     INTEGER NOT NULL DEFAULT 1,
    unit_price       INTEGER NOT NULL DEFAULT 450,
    total_price      REAL NOT NULL DEFAULT 0,
    seat_codes       TEXT,
    customer_name    TEXT NOT NULL,
    contact          TEXT NOT NULL,
    special_requests TEXT,
    status           TEXT NOT NULL DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Cancelled','Completed')),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ref_code ON bookings(ref_code);
CREATE INDEX IF NOT EXISTS idx_user_id  ON bookings(user_id);
"""

def init_db():
    """Auto-create all tables using the embedded SQLite schema."""
    db = sqlite3.connect(DB_PATH)
    db.executescript(SQLITE_SCHEMA)
    db.commit()

    # Seed cinemas if empty
    count = db.execute("SELECT COUNT(*) FROM cinemas").fetchone()[0]
    if count == 0:
        cinemas = [
            ('SM Seaside Cebu',           'SRP, Cebu City',            6),
            ('Gaisano Grand Minglanilla', 'Minglanilla, Cebu',         4),
            ('Nustar Cebu Cinema',        'SRP, Cebu City',            5),
            ('Cebu IL CORSO Cinema',      'South Road Properties',     4),
            ('UC Cantao-an',              'Naga, Cebu',                2),
            ('TOPS Cebu Skydom',          'Busay, Cebu City',          3),
        ]
        db.executemany("INSERT INTO cinemas (name, location, screens) VALUES (?,?,?)", cinemas)
        db.commit()
    db.close()

# ── DB CONNECTION ─────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row          # dict-like rows
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

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
            cur = db.execute("SELECT id FROM users WHERE id=?", (session['user_id'],))
            exists = cur.fetchone()
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
    # Mark completed showings
    db.execute("""
        UPDATE showings
           SET status = 'completed'
         WHERE status IN ('open','scheduled','full')
           AND (show_date || ' ' || show_time) < ?
    """, (now,))
    # Release expired seat locks
    db.execute("""
        UPDATE seats SET status='available', locked_until=NULL
         WHERE status='locked' AND locked_until < ?
    """, (now,))
    # Complete bookings for completed showings
    db.execute("""
        UPDATE bookings SET status='Completed'
         WHERE status='Confirmed'
           AND showing_id IN (SELECT id FROM showings WHERE status='completed')
    """)
    db.commit()

# ── SEED SEATS ────────────────────────────────────────────────
def seed_seats(db, showing_id):
    """Seed 50 seats (rows A-E, 10 seats each). A & B = VIP."""
    rows_config = [
        ('A', 'VIP'),  ('B', 'VIP'),
        ('C', 'Standard'), ('D', 'Standard'), ('E', 'Standard'),
    ]
    for row_label, category in rows_config:
        for num in range(1, 11):
            seat_code = f"{row_label}{num}"
            db.execute("""
                INSERT OR IGNORE INTO seats
                    (showing_id, row_label, seat_number, seat_code, category, status)
                VALUES (?, ?, ?, ?, ?, 'available')
            """, (showing_id, row_label, num, seat_code, category))
    db.commit()

def ensure_seats(db, showing_id):
    """Seed 50 seats for a showing if not yet seeded."""
    row = db.execute("SELECT COUNT(*) FROM seats WHERE showing_id=?", (showing_id,)).fetchone()
    if row[0] == 0:
        seed_seats(db, showing_id)

# ── GENERATE FUTURE SHOWINGS ──────────────────────────────────
def ensure_future_showings(db, movie_id, cinema_id, days_ahead=3):
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=3)).isoformat()
    row = db.execute("""
        SELECT COUNT(*) FROM showings
         WHERE movie_id=? AND cinema_id=?
           AND show_date > ?
           AND show_date <= ?
           AND status IN ('open','scheduled')
    """, (movie_id, cinema_id, today, limit)).fetchone()

    if row[0] < 2:
        timeslots = ['10:00:00', '13:30:00', '16:30:00', '19:30:00', '22:00:00']
        for d in range(0, days_ahead + 1):
            show_date = (date.today() + timedelta(days=d)).isoformat()
            for t in timeslots:
                db.execute("""
                    INSERT OR IGNORE INTO showings
                        (movie_id, cinema_id, show_date, show_time, status)
                    VALUES (?, ?, ?, ?, 'open')
                """, (movie_id, cinema_id, show_date, t))
        db.commit()

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

    # ── All active movies with availability info ──────────────
    all_movies_raw = db.execute("""
        SELECT m.id, m.title, m.genre, m.rating, m.poster_path, m.duration_mins,
               (SELECT MIN(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status IN ('open','scheduled')
                   AND (s.show_date || ' ' || s.show_time) > ?
               ) AS next_date,
               (SELECT COUNT(*) FROM showings s
                 WHERE s.movie_id=m.id AND s.show_date=?
                   AND s.status IN ('open','full')) AS today_count,
               (SELECT MAX(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status='completed') AS last_played
        FROM movies m WHERE m.status='active'
        ORDER BY today_count DESC, next_date ASC
    """, (now, today)).fetchall()
    all_movies = [dict(r) for r in all_movies_raw]

    selected_movie    = None
    showings_by_date  = {}
    selected_showing  = None
    seat_rows         = []

    # ── Step 2: Showings for selected movie ───────────────────
    if movie_id:
        row = db.execute("SELECT * FROM movies WHERE id=? AND status='active'", (movie_id,)).fetchone()
        selected_movie = dict(row) if row else None

        if selected_movie:
            # Auto-generate showings for every cinema if none exist
            cinemas_all = db.execute("SELECT id FROM cinemas").fetchall()
            for _c in cinemas_all:
                ensure_future_showings(db, movie_id, _c["id"], days_ahead=3)

            raw_showings = db.execute("""
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
                WHERE s.movie_id=?
                  AND s.status IN ('open','scheduled','full')
                  AND (s.show_date || ' ' || s.show_time) > ?
                  AND s.show_date <= ?
                ORDER BY s.show_date, s.show_time
            """, (movie_id, now, limit)).fetchall()

            for sh in raw_showings:
                sh = dict(sh)
                if sh['total_seeded'] == 0:
                    ensure_seats(db, sh['id'])
                    sh['avail_count'] = 50

                if sh['avail_count'] == 0 and sh['booked_count'] == 0:
                    sh['avail_count'] = sh['total_seats']

                d_str   = sh['show_date']
                d_label = datetime.strptime(d_str, '%Y-%m-%d').strftime('%A, %B %d %Y')

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

        row = db.execute("""
            SELECT s.id, s.show_date, s.show_time, s.status AS show_status,
                   s.total_seats,
                   c.name AS cinema_name, c.location AS cinema_location,
                   m.title AS movie_title, m.genre, m.rating, m.poster_path,
                   m.id AS movie_id_val
            FROM showings s
            JOIN cinemas c ON c.id=s.cinema_id
            JOIN movies  m ON m.id=s.movie_id
            WHERE s.id=?
        """, (showing_id,)).fetchone()

        if row:
            selected_showing = dict(row)
            selected_showing['show_time_fmt'] = _fmt_time(selected_showing['show_time'])
            selected_showing['show_date_fmt'] = datetime.strptime(
                selected_showing['show_date'], '%Y-%m-%d').strftime('%A, %B %d %Y')

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

        all_seats_raw = db.execute("""
            SELECT st.id, st.row_label, st.seat_number, st.seat_code,
                   st.category, st.status, st.locked_until
            FROM seats st
            WHERE st.showing_id=?
            ORDER BY st.row_label, st.seat_number
        """, (showing_id,)).fetchall()

        from collections import defaultdict
        rows_dict = defaultdict(list)
        for s in all_seats_raw:
            s = dict(s)
            rows_dict[s['row_label']].append(s)
        seat_rows = [(lbl, rows_dict[lbl]) for lbl in sorted(rows_dict.keys())]

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
        # Release expired / this-user's previous locks
        db.execute("""
            UPDATE seats SET status='available', locked_until=NULL
             WHERE showing_id=? AND status='locked'
               AND (locked_until < ?
                    OR id IN (
                        SELECT seat_id FROM bookings
                         WHERE user_id=? AND status='Confirmed'
                    ))
        """, (showing_id, now, session['user_id']))

        seat = db.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
        if not seat or seat['status'] != 'available':
            db.close()
            return jsonify({'ok': False, 'msg': 'Seat no longer available'})

        lock_exp = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute("""
            UPDATE seats SET status='locked', locked_until=? WHERE id=?
        """, (lock_exp, seat_id))
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
    db.execute("""
        UPDATE seats SET status='available', locked_until=NULL
         WHERE id=? AND status='locked'
    """, (seat_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ── API: SEAT STATUS ───────────────────────────────────────────
@app.route('/api/seat-status/<int:showing_id>')
@login_required
def seat_status(showing_id):
    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute("""
        UPDATE seats SET status='available', locked_until=NULL
         WHERE showing_id=? AND status='locked' AND locked_until < ?
    """, (showing_id, now))
    db.commit()

    seats = [dict(r) for r in db.execute("""
        SELECT id, seat_code, status, category, row_label, seat_number
        FROM seats WHERE showing_id=?
        ORDER BY row_label, seat_number
    """, (showing_id,)).fetchall()]
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
    if not seat_ids_raw:
        errors['seats'] = 'Please select at least one seat.'
    if not showing_id:
        errors['showing'] = 'Invalid showing.'
    if not customer_name or len(customer_name) < 2:
        errors['customer_name'] = 'Valid name is required (min 2 chars).'
    if not contact or not re.match(r'^(\+63|0)\d{10}$', contact):
        errors['contact'] = 'Enter a valid PH mobile (09XXXXXXXXX or +639XXXXXXXXX).'
    if ticket_type not in TICKET_PRICES:
        errors['ticket_type'] = 'Invalid ticket type.'

    seat_ids = [int(x) for x in seat_ids_raw.split(',') if x.strip().isdigit()]
    if not seat_ids:
        errors['seats'] = 'No valid seats selected.'
    elif len(seat_ids) > 10:
        errors['seats'] = 'Maximum 10 seats per booking.'

    if errors:
        flash(' | '.join(errors.values()), 'error')
        return redirect(url_for('booking', showing_id=showing_id))

    db = get_db()

    try:
        if not db.execute("SELECT id FROM users WHERE id=?", (session['user_id'],)).fetchone():
            db.close()
            session.clear()
            flash('Your session has expired. Please log in again.', 'warning')
            return redirect(url_for('login'))

        showing = db.execute("SELECT * FROM showings WHERE id=?", (showing_id,)).fetchone()
        if not showing or showing['status'] not in ('open', 'scheduled', 'full'):
            flash('This showing is no longer available.', 'error')
            db.close()
            return redirect(url_for('booking'))

        for sid in seat_ids:
            seat = db.execute("SELECT * FROM seats WHERE id=?", (sid,)).fetchone()
            if not seat or seat['status'] == 'booked':
                code = seat['seat_code'] if seat else str(sid)
                flash(f'Seat {code} was just taken. Please re-select.', 'error')
                db.close()
                return redirect(url_for('booking', showing_id=showing_id))

        unit_price = TICKET_PRICES[ticket_type]
        ref_code   = 'TKT-' + uuid.uuid4().hex[:8].upper()

        for sid in seat_ids:
            db.execute("UPDATE seats SET status='booked', locked_until=NULL WHERE id=?", (sid,))
            db.execute("""
                INSERT INTO bookings
                    (user_id, showing_id, seat_id, ticket_type, unit_price,
                     customer_name, contact, special_requests, ref_code)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (session['user_id'], showing_id, sid, ticket_type, unit_price,
                  customer_name, contact, special, ref_code))

        avail = db.execute("""
            SELECT COUNT(*) FROM seats WHERE showing_id=? AND status='available'
        """, (showing_id,)).fetchone()[0]
        if avail == 0:
            db.execute("UPDATE showings SET status='full' WHERE id=?", (showing_id,))

        db.commit()

        sh = db.execute("""
            SELECT s.show_date, s.show_time, c.name AS cinema, m.title AS movie
            FROM showings s
            JOIN cinemas c ON c.id=s.cinema_id
            JOIN movies  m ON m.id=s.movie_id
            WHERE s.id=?
        """, (showing_id,)).fetchone()

        placeholders = ','.join(['?'] * len(seat_ids))
        seat_info = db.execute(
            f"SELECT seat_code, category FROM seats WHERE id IN ({placeholders})",
            seat_ids
        ).fetchall()
        seat_codes = ', '.join(f"{s['seat_code']} ({s['category']})" for s in seat_info)

        booking_data = {
            'movie':        sh['movie'],
            'cinema':       sh['cinema'],
            'date':         datetime.strptime(sh['show_date'], '%Y-%m-%d').strftime('%A, %B %d %Y'),
            'showtime':     _fmt_time(sh['show_time']),
            'seats':        seat_codes,
            'ticket_count': len(seat_ids),
            'ticket_type':  ticket_type,
            'total_price':  f'{unit_price * len(seat_ids):,}',
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
    rows = db.execute("""
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
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
    """, (session['user_id'],)).fetchall()
    db.close()

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['ref_code']].append(dict(r))

    bookings_list = []
    for ref, seats in grouped.items():
        first = seats[0]
        total = sum(s['unit_price'] for s in seats)
        bookings_list.append({
            'ref':         ref,
            'movie':       first['movie'],
            'cinema':      first['cinema'],
            'date':        datetime.strptime(first['show_date'], '%Y-%m-%d').strftime('%b %d, %Y'),
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
            # ── Admin shortcut ────────────────────────────────────
            if identifier == 'admin@gmail.com' and password == ADMIN_PASSWORD:
                session['is_admin']   = True
                session['admin_name'] = 'Admin'
                return redirect(url_for('admin_dashboard'))
            try:
                db  = get_db()
                user = db.execute(
                    'SELECT * FROM users WHERE email=? OR mobile=?',
                    (identifier, identifier)
                ).fetchone()
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
                exists = db.execute(
                    'SELECT id FROM users WHERE email=? OR mobile=?', (email, mobile)
                ).fetchone()
                if exists:
                    errors['identifier'] = 'Already registered. Please log in.'
                else:
                    hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    address = f"{barangay}, {city}, {province}"
                    db.execute("""
                        INSERT INTO users (email, mobile, full_name, age, gender, address, password)
                        VALUES (?,?,?,?,?,?,?)
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


# ── TIME HELPER ────────────────────────────────────────────────
def _fmt_time(t):
    """Format a HH:MM:SS string to 12-hour AM/PM."""
    if not t:
        return ''
    parts = str(t).split(':')
    hrs  = int(parts[0])
    mins = int(parts[1]) if len(parts) > 1 else 0
    suffix = 'AM' if hrs < 12 else 'PM'
    return f'{hrs % 12 or 12}:{mins:02d} {suffix}'


# ═══════════════════════════════════════════════════════════════
#  ADMIN SECTION
# ═══════════════════════════════════════════════════════════════
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'tickit2024'   # Change this in production!

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ── Admin Login ─────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['is_admin']    = True
            session['admin_name']  = 'Admin'
            return redirect(url_for('admin_dashboard'))
        error = 'Invalid username or password.'
    return render_template('admin_login.html', error=error)

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
    stats['available_seats']   = db.execute(
        "SELECT COUNT(*) FROM seats WHERE status='available'").fetchone()[0]
    stats['total_sales']       = db.execute(
        "SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status IN ('Confirmed','Completed')").fetchone()[0]
    stats['total_bookings']    = db.execute(
        "SELECT COUNT(*) FROM bookings").fetchone()[0]
    stats['confirmed_bookings']= db.execute(
        "SELECT COUNT(*) FROM bookings WHERE status='Confirmed'").fetchone()[0]
    stats['active_movies']     = db.execute(
        "SELECT COUNT(*) FROM movies WHERE status='active'").fetchone()[0]
    stats['total_movies']      = db.execute(
        "SELECT COUNT(*) FROM movies").fetchone()[0]
    stats['total_users']       = db.execute(
        "SELECT COUNT(*) FROM users").fetchone()[0]
    stats['today_showings']    = db.execute(
        "SELECT COUNT(*) FROM showings WHERE show_date=? AND status IN ('open','full')",
        (date.today().isoformat(),)).fetchone()[0]

    recent_bookings_raw = db.execute("""
        SELECT b.id, b.booking_ref, b.customer_name, b.total_price, b.status,
               b.ticket_count, b.ticket_type, b.seat_codes,
               m.title AS movie_title,
               s.show_date, s.show_time
        FROM bookings b
        JOIN showings s ON b.showing_id = s.id
        JOIN movies   m ON s.movie_id   = m.id
        ORDER BY b.id DESC LIMIT 10
    """).fetchall()
    recent_bookings = [dict(r) for r in recent_bookings_raw]

    active_movies_raw = db.execute("""
        SELECT m.*,
               COALESCE((SELECT COUNT(*) FROM showings sh
                          JOIN seats st ON st.showing_id=sh.id
                         WHERE sh.movie_id=m.id AND st.status='available'), 0) AS avail_seats
        FROM movies m WHERE m.status='active' ORDER BY m.title
    """).fetchall()
    active_movies = [dict(r) for r in active_movies_raw]
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
    movies_raw = db.execute("""
        SELECT m.*,
               COALESCE((SELECT COUNT(*) FROM showings sh
                          JOIN seats st ON st.showing_id=sh.id
                         WHERE sh.movie_id=m.id AND st.status='available'), 0) AS avail_seats
        FROM movies m ORDER BY m.title
    """).fetchall()
    movies = [dict(r) for r in movies_raw]
    db.close()
    return render_template('admin_movies.html', movies=movies)

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
        import werkzeug.utils
        filename = werkzeug.utils.secure_filename(poster_file.filename)
        save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
        os.makedirs(save_dir, exist_ok=True)
        poster_file.save(os.path.join(save_dir, filename))
        poster_path = f'images/movies/{filename}'

    try:
        db = get_db()
        db.execute("""
            INSERT INTO movies (title, genre, cast_members, duration_mins, rating,
                                release_date, status, description, poster_path)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (title, genre, cast_members, int(duration), float(rating),
              release_date, status, description, poster_path))
        db.commit()
        db.close()
        flash(f'Movie "{title}" added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding movie: {e}', 'error')
    return redirect(url_for('admin_movies'))

@app.route('/admin/movies/edit/<int:movie_id>', methods=['GET', 'POST'])
@admin_required
def admin_movies_edit(movie_id):
    db = get_db()
    if request.method == 'POST':
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
            db.close()
            return redirect(url_for('admin_movies'))

        poster_file = request.files.get('poster')
        if poster_file and poster_file.filename:
            import werkzeug.utils
            filename = werkzeug.utils.secure_filename(poster_file.filename)
            save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
            os.makedirs(save_dir, exist_ok=True)
            poster_file.save(os.path.join(save_dir, filename))
            db.execute("""
                UPDATE movies SET title=?, genre=?, cast_members=?, duration_mins=?,
                                  rating=?, release_date=?, status=?, description=?, poster_path=?
                WHERE id=?
            """, (title, genre, cast_members, int(duration), float(rating),
                  release_date, status, description, f'images/movies/{filename}', movie_id))
        else:
            db.execute("""
                UPDATE movies SET title=?, genre=?, cast_members=?, duration_mins=?,
                                  rating=?, release_date=?, status=?, description=?
                WHERE id=?
            """, (title, genre, cast_members, int(duration), float(rating),
                  release_date, status, description, movie_id))
        db.commit()
        db.close()
        flash(f'Movie "{title}" updated successfully!', 'success')
        return redirect(url_for('admin_movies'))
    db.close()
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
        movie = db.execute("SELECT title FROM movies WHERE id=?", (movie_id,)).fetchone()
        if movie:
            db.execute("DELETE FROM seats WHERE showing_id IN (SELECT id FROM showings WHERE movie_id=?)", (movie_id,))
            db.execute("DELETE FROM bookings WHERE showing_id IN (SELECT id FROM showings WHERE movie_id=?)", (movie_id,))
            db.execute("DELETE FROM showings WHERE movie_id=?", (movie_id,))
            db.execute("DELETE FROM movies WHERE id=?", (movie_id,))
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
    bookings_raw = db.execute("""
        SELECT b.id, b.booking_ref, b.customer_name, b.contact, b.total_price,
               b.status, b.ticket_count, b.ticket_type, b.seat_codes,
               m.title AS movie_title,
               c.name  AS cinema_name,
               s.show_date, s.show_time
        FROM bookings b
        JOIN showings s ON b.showing_id = s.id
        JOIN movies   m ON s.movie_id   = m.id
        JOIN cinemas  c ON s.cinema_id  = c.id
        ORDER BY b.id DESC
    """).fetchall()
    bookings = [dict(r) for r in bookings_raw]
    db.close()
    return render_template('admin_bookings.html', bookings=bookings)

@app.route('/admin/bookings/cancel', methods=['POST'])
@admin_required
def admin_bookings_cancel():
    booking_id = request.form.get('booking_id', type=int)
    if not booking_id:
        flash('Invalid booking.', 'error')
        return redirect(url_for('admin_bookings'))
    try:
        db = get_db()
        db.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (booking_id,))
        db.execute("""
            UPDATE seats SET status='available', locked_until=NULL
             WHERE id IN (
                SELECT seat_id FROM booking_seats WHERE booking_id=?
             )
        """, (booking_id,))
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
    users_raw = db.execute("""
        SELECT u.*,
               COALESCE((SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id), 0) AS booking_count
        FROM users u ORDER BY u.id DESC
    """).fetchall()
    users = [dict(r) for r in users_raw]
    db.close()
    return render_template('admin_users.html', users=users)

@app.route('/admin/users/delete', methods=['POST'])
@admin_required
def admin_users_delete():
    user_id = request.form.get('user_id', type=int)
    if not user_id:
        flash('Invalid user.', 'error')
        return redirect(url_for('admin_users'))
    try:
        db = get_db()
        user = db.execute("SELECT full_name FROM users WHERE id=?", (user_id,)).fetchone()
        if user:
            db.execute("DELETE FROM bookings WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            db.commit()
            flash(f'User "{user["full_name"]}" deleted.', 'success')
        db.close()
    except Exception as e:
        flash(f'Error deleting user: {e}', 'error')
    return redirect(url_for('admin_users'))


# ── STARTUP ────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()   # Creates tickit.db and all tables automatically on first run
    app.run(debug=True, host='0.0.0.0', port=5000)
