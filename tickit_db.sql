-- ============================================================
--  TICK.IT  —  MySQL Database Schema
--  Run this in MySQL Workbench or: mysql -u root -p < tickit_db.sql
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
    age        TINYINT UNSIGNED NOT NULL,
    gender     ENUM('Male','Female','Non-binary','Prefer not to say') NOT NULL,
    address    VARCHAR(500) NOT NULL,
    password   VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_contact CHECK (email IS NOT NULL OR mobile IS NOT NULL)
);