-- ============================================================
--  TICK.IT — MySQL Schema
--  Run this in MySQL Workbench before starting the Flask app
-- ============================================================

CREATE DATABASE IF NOT EXISTS tickit_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE tickit_db;

-- ── USERS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    email      VARCHAR(255) UNIQUE,
    mobile     VARCHAR(20)  UNIQUE,
    full_name  VARCHAR(255) NOT NULL,
    age        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    gender     ENUM('Male','Female','Non-binary','Prefer not to say') NOT NULL DEFAULT 'Prefer not to say',
    address    VARCHAR(500) NOT NULL DEFAULT '',
    password   VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_contact CHECK (email IS NOT NULL OR mobile IS NOT NULL)
);

-- ── MOVIES ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS movies (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    genre         VARCHAR(100) NOT NULL,
    rating        DECIMAL(3,1) NOT NULL DEFAULT 0.0,
    poster_path   VARCHAR(500) NOT NULL DEFAULT 'images/no_poster.png',
    duration_mins SMALLINT UNSIGNED NOT NULL DEFAULT 120,
    description   TEXT,
    cast_members  VARCHAR(500),
    release_date  DATE,
    status        ENUM('active','inactive') NOT NULL DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── CINEMAS ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cinemas (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    name     VARCHAR(255) NOT NULL,
    location VARCHAR(500) NOT NULL,
    screens  TINYINT UNSIGNED NOT NULL DEFAULT 1
);

-- ── SHOWINGS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS showings (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    movie_id    INT NOT NULL,
    cinema_id   INT NOT NULL,
    show_date   DATE NOT NULL,
    show_time   TIME NOT NULL,
    total_seats TINYINT UNSIGNED NOT NULL DEFAULT 50,
    status      ENUM('scheduled','open','full','completed') NOT NULL DEFAULT 'open',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (movie_id)  REFERENCES movies(id)  ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE CASCADE,
    UNIQUE KEY uq_showing (movie_id, cinema_id, show_date, show_time)
);

-- ── SEATS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seats (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    showing_id   INT NOT NULL,
    row_label    CHAR(1) NOT NULL,
    seat_number  TINYINT UNSIGNED NOT NULL,
    seat_code    VARCHAR(6) NOT NULL,
    category     ENUM('VIP','Standard') NOT NULL DEFAULT 'Standard',
    status       ENUM('available','locked','booked') NOT NULL DEFAULT 'available',
    locked_until DATETIME NULL,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE,
    UNIQUE KEY uq_seat (showing_id, seat_code)
);

-- ── BOOKINGS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bookings (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT NOT NULL,
    showing_id       INT NOT NULL,
    seat_id          INT NOT NULL,
    booking_ref      VARCHAR(20),
    ref_code         VARCHAR(20) NOT NULL,
    ticket_type      ENUM('Regular','Student','Senior / PWD') NOT NULL DEFAULT 'Regular',
    ticket_count     TINYINT UNSIGNED NOT NULL DEFAULT 1,
    unit_price       SMALLINT UNSIGNED NOT NULL DEFAULT 450,
    total_price      DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    seat_codes       VARCHAR(500),
    customer_name    VARCHAR(255) NOT NULL,
    contact          VARCHAR(20)  NOT NULL,
    special_requests TEXT,
    status           ENUM('Confirmed','Cancelled','Completed') NOT NULL DEFAULT 'Confirmed',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE,
    FOREIGN KEY (seat_id)    REFERENCES seats(id)    ON DELETE CASCADE,
    INDEX idx_ref_code (ref_code),
    INDEX idx_user_id  (user_id)
);

-- ── SEED CINEMAS ─────────────────────────────────────────────
INSERT IGNORE INTO cinemas (name, location, screens) VALUES
    ('SM Seaside Cebu',           'SRP, Cebu City',        6),
    ('Gaisano Grand Minglanilla', 'Minglanilla, Cebu',     4),
    ('Nustar Cebu Cinema',        'SRP, Cebu City',        5),
    ('Cebu IL CORSO Cinema',      'South Road Properties', 4),
    ('UC Cantao-an',              'Naga, Cebu',            2),
    ('TOPS Cebu Skydom',          'Busay, Cebu City',      3);
