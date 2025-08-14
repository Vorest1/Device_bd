# get_admin.py
import os
import sqlite3
import argparse
import datetime as dt
import getpass
from werkzeug.security import generate_password_hash

DEF_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "2lr.db")

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  email TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  is_admin  INTEGER NOT NULL DEFAULT 0
)
"""

def ensure_schema(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute(USERS_DDL)
    # добавим столбец is_admin, если таблица старая
    c.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in c.fetchall()]
    if "is_admin" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    conn.commit()

def upsert_admin(conn: sqlite3.Connection, username: str, email: str | None, password: str) -> int:
    c = conn.cursor()
    # есть ли такой username?
    c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    now = dt.datetime.utcnow().isoformat()
    pwd_hash = generate_password_hash(password)

    if row:
        uid = row[0]
        c.execute(
            "UPDATE users SET email=?, password_hash=?, created_at=?, is_active=1, is_admin=1 WHERE user_id=?",
            (email, pwd_hash, now, uid),
        )
        action = "updated"
    else:
        c.execute(
            "INSERT INTO users (username, email, password_hash, created_at, is_active, is_admin) VALUES (?, ?, ?, ?, 1, 1)",
            (username, email, pwd_hash, now),
        )
        uid = c.lastrowid
        action = "created"
    conn.commit()
    print(f"[OK] Admin {action}: user_id={uid}, username={username}")
    return uid

def list_admins(conn: sqlite3.Connection):
    c = conn.cursor()
    try:
        rows = c.execute("SELECT user_id, username, email FROM users WHERE is_admin=1 ORDER BY user_id").fetchall()
    except sqlite3.Error:
        rows = []
    print("[INFO] Current admins:", rows)

def main():
    ap = argparse.ArgumentParser(description="Create or update an admin user in SQLite DB.")
    ap.add_argument("-d", "--db", default=DEF_DB, help=f"Path to SQLite DB (default: {DEF_DB})")
    ap.add_argument("-u", "--username", required=True, help="Admin username (unique).")
    ap.add_argument("-e", "--email", default=None, help="Admin email (optional, must be unique if set).")
    ap.add_argument("-p", "--password", help="Admin password. If omitted, you'll be prompted.")
    args = ap.parse_args()

    if not args.password:
        # интерактивный ввод пароля без эха
        pwd1 = getpass.getpass("Enter password: ")
        pwd2 = getpass.getpass("Confirm password: ")
        if not pwd1 or pwd1 != pwd2:
            raise SystemExit("Passwords do not match or empty. Aborting.")
        args.password = pwd1

    db_path = os.path.abspath(args.db)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(">>> Using DB:", db_path)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        try:
            upsert_admin(conn, args.username.strip(), (args.email or None), args.password)
        except sqlite3.IntegrityError as e:
            # конфликт email'а — поясняем
            if "UNIQUE constraint failed: users.email" in str(e):
                raise SystemExit("Email is already used by another user. Use a different email or clear it with --email ''.")
            raise
        list_admins(conn)

if __name__ == "__main__":
    main()
