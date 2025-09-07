import os
import re
from datetime import date as _Date, datetime as _DT
from functools import wraps
from typing import Optional, List, Tuple, Dict, Any

import psycopg2
import psycopg2.extras
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, flash
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------------------------------------
# Конфиг
# -------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev_key_change_me")

PG_DSN = os.getenv(
    "PG_DSN",
    "dbname=device_db user=postgres password=admin host=127.0.0.1 port=5432"
)
SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "admin")

def get_conn():
    return psycopg2.connect(PG_DSN)

def tup_cur(conn):
    return conn.cursor()

def _sort_ci_tuples(rows, idx=1):
    return sorted(rows, key=lambda r: (str(r[idx]).strip().casefold(), r[idx]))

def _sort_ci_dicts(items, key_name="name"):
    return sorted(items, key=lambda x: (str(x.get(key_name, "")).strip().casefold(),
                                        x.get(key_name, "")))

# -------------------------------------------------
# Flask-Login
# -------------------------------------------------
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

class User(UserMixin):
    def __init__(self, user_id, username, email, password_hash, created_at, is_active, is_admin):
        self.id = user_id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.created_at = created_at
        self.is_active_ = bool(is_active)
        self.is_admin = bool(is_admin)
    def is_active(self):
        return self.is_active_

def row_to_user(row):
    if not row:
        return None
    return User(
        user_id=row[0], username=row[1], email=row[2],
        password_hash=row[3], created_at=row[4],
        is_active=row[5], is_admin=row[6]
    )

@login_manager.user_loader
def load_user(user_id):
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT user_id, username, email, password_hash, created_at, is_active, is_admin
            FROM users
            WHERE user_id = %s
        """, (user_id,))
        return row_to_user(cur.fetchone())

# -------------------------------------------------
# Декораторы прав
# -------------------------------------------------
def admin_required(view):
    @wraps(view)
    def _wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login', next=request.url))
        if not getattr(current_user, 'is_admin', False):
            flash('Доступ только для администратора.', 'danger')
            return redirect(url_for('index'))
        return view(*args, **kwargs)
    return _wrapped

@app.context_processor
def inject_roles():
    is_superadmin = current_user.is_authenticated and (getattr(current_user, 'username', '').lower() == SUPERADMIN_USERNAME)
    is_admin_effective = current_user.is_authenticated and getattr(current_user, 'is_admin', False)
    return dict(is_superadmin=is_superadmin, is_admin=is_admin_effective)

@app.context_processor
def inject_flags():
    return {'is_admin': (current_user.is_authenticated and getattr(current_user, 'is_admin', False))}

# -------------------------------------------------
# Интроспекция БД (PostgreSQL)
# -------------------------------------------------
def get_pk_name(conn, table_name: str) -> Optional[str]:
    sql = """
    SELECT a.attname
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indrelid
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE i.indisprimary = true
      AND n.nspname = 'public'
      AND c.relname = %s
    ORDER BY array_position(i.indkey, a.attnum)
    LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table_name,))
        row = cur.fetchone()
        return row[0] if row else None

def next_id(conn, table_name: str) -> tuple[int, str]:
    """
    Возвращает (следующий_id, имя_pk) для заданной таблицы.
    Следующий_id = COALESCE(MAX(pk), 0) + 1
    """
    pk = get_pk_name(conn, table_name) or f"{table_name.rstrip('s')}_id"
    with conn.cursor() as cur:
        cur.execute(f"SELECT COALESCE(MAX({pk}), 0) + 1 FROM {table_name}")
        nid = cur.fetchone()[0]
    return int(nid), pk

def list_user_tables(conn) -> List[str]:
    sql = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]

def count_columns(conn, table_name: str) -> int:
    sql = """
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = %s;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table_name,))
        return cur.fetchone()[0] or 0

# -------------------------------------------------
# Ограничения удаления
# -------------------------------------------------
PROTECTED_CHILD_TABLES = {'displays','batteries','cameras','specifications','device_retailers'}
REFERENCE_MAP = {
    'categories':         [('devices',          'category_id')],
    'manufacturers':      [('devices',          'manufacturer_id')],
    'retailers':          [('device_retailers', 'retailer_id')],
    'color':              [('devices',          'color_id')],
    'country':            [('manufacturers',    'country_id')],
    'os_name':            [('operating_systems','os_name_id')],
    'proc_model':         [('specifications',   'proc_model_id')],
    'storage_type':       [('specifications',   'storage_type_id')],
    'techn_matr':         [('displays',         'techn_matr_id')],
    'operating_systems':  [('devices', 'os_id')],
    'model':              [('devices',          'model_id')]
}
def value_in_use(conn, table_name: str, pk_value: str) -> Tuple[bool, str]:
    refs = REFERENCE_MAP.get(table_name, [])
    if not refs:
        return (False, '')
    pk = get_pk_name(conn, table_name) or f"{table_name.rstrip('s')}_id"
    with conn.cursor() as cur:
        for ref_table, ref_col in refs:
            cur.execute(f"SELECT 1 FROM {ref_table} WHERE {ref_col} = %s LIMIT 1", (pk_value,))
            if cur.fetchone():
                return (True, f"{ref_table}.{ref_col}")
    return (False, '')

# -------------------------------------------------
# Аутентификация
# -------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip() or None
        password = request.form.get('password') or ''
        if not username or not password:
            flash('Заполните логин и пароль.', 'danger')
            return redirect(url_for('register'))

        with get_conn() as conn:
            cur = tup_cur(conn)
            cur.execute("SELECT 1 FROM users WHERE username = %s OR (email IS NOT NULL AND email = %s)", (username, email))
            if cur.fetchone():
                flash('Такой логин или email уже заняты.', 'danger')
                return redirect(url_for('register'))

            cur.execute("""
                INSERT INTO users (username, email, password_hash, created_at, is_active, is_admin)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING user_id
            """, (username, email, generate_password_hash(password), _DT.utcnow(), True, True))
            new_id = cur.fetchone()[0]
            conn.commit()

        user = load_user(new_id)
        login_user(user)
        flash('Вы успешно зарегистрированы и вошли в систему.', 'success')
        return redirect(url_for('profile'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_val = (request.form.get('login') or '').strip()
        password = request.form.get('password') or ''
        with get_conn() as conn:
            cur = tup_cur(conn)
            cur.execute("""
                SELECT user_id, username, email, password_hash, created_at, is_active, is_admin
                FROM users
                WHERE username = %s OR email = %s
            """, (login_val, login_val))
            row = cur.fetchone()
        user = row_to_user(row)
        if not user or not check_password_hash(user.password_hash, password):
            flash('Неверные логин или пароль.', 'danger')
            return redirect(url_for('login'))

        login_user(user)
        flash('Добро пожаловать!', 'success')
        return redirect(request.args.get('next') or url_for('profile'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT d.device_id, ml.name AS model, m.name AS manufacturer, c2.name AS category, d.current_price
            FROM devices d
            JOIN model ml ON d.model_id = ml.model_id
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            JOIN categories     c2 ON d.category_id = c2.category_id
            WHERE d.created_by = %s
            ORDER BY d.device_id DESC
        """, (current_user.id,))
        rows = cur.fetchall()
    devices = [{'device_id': r[0], 'model': r[1], 'manufacturer': r[2], 'category': r[3], 'current_price': r[4]} for r in rows]
    return render_template('profile.html', user=current_user, devices=devices)

# -------------------------------------------------
# Главная
# -------------------------------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    mode = request.form.get('mode') if request.method == 'POST' else None
    info_by = request.form.get('info_by', 'retailer') if mode == 'info' else None
    info_results = None

    purpose_map = {
        'devices': 'главная',
        'categories': 'справочник',
        'manufacturers': 'справочник',
        'retailers': 'справочник',
        'color': 'справочник',
        'models': 'справочник',
        'country': 'справочник',
        'os_name': 'справочник',
        'proc_model': 'справочник',
        'storage_type': 'справочник',
        'techn_matr': 'справочник',
        'device_retailers': 'дополнительная',
        'batteries': 'дополнительная',
        'cameras': 'дополнительная',
        'displays': 'дополнительная',
        'specifications': 'дополнительная',
        'operating_systems': 'дополнительная',
        'users': 'дополнительная'
    }
    rus_desc = {
        'batteries': 'Характеристики батареи',
        'cameras': 'Характеристики камер',
        'categories': 'Справочник категорий',
        'color': 'Справочник цветов',
        'country': 'Справочник стран',
        'device_retailers': 'Цены/наличие у продавцов',
        'devices': 'Устройства (основная)',
        'displays': 'Характеристики дисплея',
        'manufacturers': 'Справочник производителей',
        'models': 'Справочник моделей',
        'operating_systems': 'Версии ОС для устройств',
        'os_name': 'Справочник названий ОС',
        'proc_model': 'Модели процессоров',
        'retailers': 'Справочник продавцов',
        'specifications': 'Прочие характеристики',
        'storage_type': 'Типы накопителей',
        'techn_matr': 'Типы матрицы дисплея',
        'users': 'Пользователи системы'
    }

    with get_conn() as conn:
        cur = tup_cur(conn)

        totals = {}
        for t in ['devices','categories','manufacturers','retailers','color','country','os_name']:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                totals[t] = cur.fetchone()[0]
            except Exception:
                totals[t] = 0

        cur.execute("SELECT MIN(current_price), AVG(current_price), MAX(current_price) FROM devices")
        price_min, price_avg, price_max = cur.fetchone()
        price_min = int(price_min or 0)
        price_avg = int(price_avg or 0)
        price_max = int(price_max or 0)

        cur.execute("SELECT MIN(release_date), MAX(release_date) FROM devices")
        release_min, release_max = cur.fetchone()

        try:
            cur.execute("SELECT MAX(last_updated) FROM device_retailers")
            last_update = cur.fetchone()[0]
        except:
            last_update = None

        cur.execute("SELECT COUNT(DISTINCT device_id) FROM device_retailers WHERE in_stock = TRUE")
        in_stock_devices = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM device_retailers")
        offers_total = cur.fetchone()[0] or 0

        table_names = list_user_tables(conn)
        tables_info = []
        for name in table_names:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                rows = cur.fetchone()[0] or 0
            except Exception:
                rows = 0
            cols = count_columns(conn, name)
            tables_info.append({
                'name': name,
                'rus': rus_desc.get(name, '—'),
                'purpose': purpose_map.get(name, 'дополнительная'),
                'rows': rows,
                'columns': cols
            })
        order = {'главная': 0, 'справочник': 1, 'дополнительная': 2}
        tables_info.sort(key=lambda x: (order.get(x['purpose'], 99), x['name']))

        if mode == 'info':
            if info_by == 'retailer':
                cur.execute("""
                    SELECT r.name, COUNT(DISTINCT dr.device_id)
                    FROM device_retailers dr
                    JOIN retailers r ON dr.retailer_id = r.retailer_id
                    GROUP BY r.name
                    ORDER BY COUNT(DISTINCT dr.device_id) DESC
                """)
            else:
                cur.execute("""
                    SELECT co.name, COUNT(DISTINCT d.device_id)
                    FROM devices d
                    JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                    JOIN country co ON m.country_id = co.country_id
                    GROUP BY co.name
                    ORDER BY COUNT(DISTINCT d.device_id) DESC
                """)
            info_results = cur.fetchall()

    return render_template(
        'index.html',
        totals=totals,
        price_min=price_min,
        price_avg=price_avg,
        price_max=price_max,
        release_min=release_min,
        release_max=release_max,
        last_update=last_update,
        in_stock_devices=in_stock_devices,
        offers_total=offers_total,
        db_size_mb=None,
        tables_info=tables_info,
        author_email='khristoforov.volodya@gmail.com',
        author_telegram='@vladimir_hrist',
        mode=mode,
        info_by=info_by,
        info_results=info_results
    )

# -------------------------------------------------
# CRUD устройств и таблиц
# -------------------------------------------------
@app.route('/add_device', methods=['GET', 'POST'])
@login_required
@admin_required
def add_device():
    min_date = '1990-01-01'
    today = _Date.today().isoformat()

    # для формы: и базовые списки, и словари для "доп. характеристик"
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("SELECT manufacturer_id, name FROM manufacturers")
        manufacturers = _sort_ci_tuples(cur.fetchall())
        cur.execute("SELECT category_id, name FROM categories")
        categories = _sort_ci_tuples(cur.fetchall())
        cur.execute("""
            SELECT osys.os_id, osn.name, osys.latest_version
            FROM operating_systems osys
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
        """)
        operating_systems = sorted(cur.fetchall(), key=lambda r: (str(r[1]).strip().casefold(), r[2]))
        cur.execute("SELECT model_id, name FROM model")
        models = _sort_ci_tuples(cur.fetchall())
        cur.execute("SELECT color_id, name FROM color")
        colors = _sort_ci_tuples(cur.fetchall())

        # справочники для доп. форм (как в edit_extras)
        cur.execute("SELECT proc_model_id, name FROM proc_model")
        proc_models = _sort_ci_tuples(cur.fetchall())
        cur.execute("SELECT storage_type_id, name FROM storage_type")
        storage_types = _sort_ci_tuples(cur.fetchall())
        cur.execute("SELECT techn_matr_id, name FROM techn_matr")
        techn_matrices = _sort_ci_tuples(cur.fetchall())
        cur.execute("SELECT retailer_id, name FROM retailers")
        retailers = _sort_ci_tuples(cur.fetchall())
        cur.execute("""
            SELECT aperture_main
            FROM cameras
            WHERE aperture_main IS NOT NULL
            GROUP BY aperture_main
            ORDER BY LOWER(aperture_main)
        """)
        aperture_main = [row[0] for row in cur.fetchall() if row[0]]

    if request.method == 'POST':
        # --- Базовые поля устройства ---
        manufacturer_id = request.form.get('manufacturer_id')
        category_id     = request.form.get('category_id')
        os_id           = request.form.get('os_id')
        model_id        = request.form.get('model_id')
        color_id        = request.form.get('color_id')
        release_date    = request.form.get('release_date')

        try:
            current_price  = int(float(request.form.get('current_price') or 0))
            weight_grams   = int(float(request.form.get('weight_grams') or 0))
            warranty_months = int(float(request.form.get('warranty_months') or 0))
        except ValueError:
            flash("Поля цена/вес/гарантия должны быть целыми числами.", "danger")
            return redirect(request.url)

        is_waterproof   = True if request.form.get('is_waterproof') == 'on' else False

        # --- Создаём устройство ---
        with get_conn() as conn:
            cur = tup_cur(conn)
            new_id, _ = next_id(conn, 'devices')
            cur.execute("""
                INSERT INTO devices
                    (device_id, manufacturer_id, category_id, os_id, model_id, release_date,
                     current_price, weight_grams, color_id, is_waterproof, warranty_months, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING device_id
            """, (new_id, manufacturer_id, category_id, os_id, model_id, release_date,
                  current_price, weight_grams, color_id, is_waterproof, warranty_months, current_user.id))
            device_id = cur.fetchone()[0]
            conn.commit()

        # ===== Доп. характеристики: сохраняем только если что-то заполнено =====
        def _has_any(names):
            for n in names:
                v = request.form.get(n)
                if v is None:
                    # чекбоксы: проверим .getlist на on
                    continue
                if n in ('has_ai_enhance', 'wireless_charging'):
                    if request.form.get(n): return True
                elif str(v).strip() != '':
                    return True
            return False

        # --- specifications ---
        if _has_any(['proc_model_id','processor_cores','ram_gb','storage_gb','storage_type_id']):
            data = {}
            pm = request.form.get('proc_model_id') or None
            st = request.form.get('storage_type_id') or None
            if pm: data['proc_model_id'] = int(pm)
            if st: data['storage_type_id'] = int(st)

            def _int_in(name, lo, hi):
                val = request.form.get(name)
                if not val: return True
                try:
                    iv = int(val)
                    if not (lo <= iv <= hi): raise ValueError
                except:
                    flash(f'{name}: {lo}-{hi}', 'danger'); return False
                data[name] = iv; return True

            if not _int_in('processor_cores', 1, 20) or not _int_in('ram_gb', 1, 32) or not _int_in('storage_gb', 1, 2048):
                return redirect(url_for('add_device'))

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_sid, _ = next_id(conn, 'specifications')
                fields = ['spec_id', 'device_id'] + list(data.keys())
                ph = ", ".join(['%s'] * len(fields))
                cur.execute(f"INSERT INTO specifications ({', '.join(fields)}) VALUES ({ph})",
                            (new_sid, device_id, *data.values()))
                conn.commit()

        # --- displays ---
        if _has_any(['diagonal_inches','resolution','techn_matr_id','refresh_rate_hz','brightness_nits']):
            try:
                diagonal_inches = request.form.get('diagonal_inches')
                diagonal_inches = float(diagonal_inches) if diagonal_inches else None
                if diagonal_inches is not None and not (1.0 <= diagonal_inches <= 100.0): raise ValueError
                resolution = request.form.get('resolution') or None
                techn_matr_id = int(request.form.get('techn_matr_id')) if request.form.get('techn_matr_id') else None
                rr = request.form.get('refresh_rate_hz')
                refresh_rate_hz = int(rr) if rr else None
                if refresh_rate_hz is not None and not (1 <= refresh_rate_hz <= 360): raise ValueError
                bn = request.form.get('brightness_nits')
                brightness_nits = int(bn) if bn else None
                if brightness_nits is not None and not (1 <= brightness_nits <= 10000): raise ValueError
            except:
                flash('Проверьте поля дисплея (диапазоны/формат).', 'danger')
                return redirect(url_for('add_device'))

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_did, _ = next_id(conn, 'displays')
                cur.execute("""
                    INSERT INTO displays
                      (display_id, device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (new_did, device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits))
                conn.commit()

        # --- cameras ---
        if _has_any(['megapixels_main','aperture_main','optical_zoom_x','video_resolution','has_ai_enhance']):
            try:
                mp = request.form.get('megapixels_main')
                megapixels_main = float(mp) if mp else None
                if megapixels_main is not None and not (2.0 <= megapixels_main <= 200.0): raise ValueError
                aperture_main = (request.form.get('aperture_main') or '').strip() or None
                if aperture_main is not None and not (3 <= len(aperture_main) <= 6): raise ValueError
                oz = request.form.get('optical_zoom_x')
                optical_zoom_x = float(oz) if oz else None
                if optical_zoom_x is not None and not (0.0 <= optical_zoom_x <= 144.0): raise ValueError
                vr = request.form.get('video_resolution')
                video_resolution = vr.strip() if vr else None
                if video_resolution is not None and not (7 <= len(video_resolution) <= 11): raise ValueError
                has_ai_enhance = True if request.form.get('has_ai_enhance') else False
            except:
                flash('Проверьте поля камеры (диапазоны/формат).', 'danger')
                return redirect(url_for('add_device'))

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_cid, _ = next_id(conn, 'cameras')
                cur.execute("""
                    INSERT INTO cameras
                      (camera_id, device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (new_cid, device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance))
                conn.commit()

        # --- batteries ---
        if _has_any(['capacity_mah','fast_charging_w','wireless_charging','estimated_life_hours']):
            try:
                cm = request.form.get('capacity_mah')
                capacity_mah = int(cm) if cm else None
                if capacity_mah is not None and not (1 <= capacity_mah <= 20000): raise ValueError
                fc = request.form.get('fast_charging_w')
                fast_charging_w = float(fc) if fc else None
                if fast_charging_w is not None and not (0.0 <= fast_charging_w <= 20.0): raise ValueError
                elh = request.form.get('estimated_life_hours')
                estimated_life_hours = float(elh) if elh else None
                if estimated_life_hours is not None and not (0.0 <= estimated_life_hours <= 96.0): raise ValueError
                wireless_charging = True if request.form.get('wireless_charging') else False
            except:
                flash('Проверьте поля батареи (диапазоны).', 'danger')
                return redirect(url_for('add_device'))

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_bid, _ = next_id(conn, 'batteries')
                cur.execute("""
                    INSERT INTO batteries
                      (battery_id, device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (new_bid, device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours))
                conn.commit()

        # --- опционально сразу одно предложение продавца ---
        if _has_any(['retailer_id','site_price','in_stock','last_updated']):
            retailer_id  = request.form.get('retailer_id')
            site_price   = request.form.get('site_price')
            in_stock     = True if request.form.get('in_stock') == 'on' else False
            last_updated = request.form.get('last_updated') or today
            try:
                price_val = float(site_price) if site_price else None
                if price_val is not None and not (0.0 <= price_val <= 1_000_000.0): raise ValueError
                _DT.strptime(last_updated, "%Y-%m-%d")
            except:
                flash('Проверьте цену продавца (0–1 000 000) и дату (YYYY-MM-DD).', 'danger')
                return redirect(url_for('add_device'))

            if retailer_id and site_price:
                with get_conn() as conn:
                    cur = tup_cur(conn)
                    new_dr, _ = next_id(conn, 'device_retailers')
                    cur.execute("""
                        INSERT INTO device_retailers (device_retailer_id, device_id, retailer_id, price, in_stock, last_updated)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, (new_dr, device_id, retailer_id, price_val, in_stock, last_updated))
                    conn.commit()

        flash('Устройство добавлено.', 'success')
        return redirect(url_for('device_detail', device_id=device_id, added=1))

    # GET — рендерим форму
    return render_template('add_device.html',
                           manufacturers=manufacturers,
                           categories=categories,
                           operating_systems=operating_systems,
                           models=models,
                           colors=colors,
                           proc_models=proc_models,
                           storage_types=storage_types,
                           techn_matrices=techn_matrices,
                           retailers=retailers,
                           aperture_main=aperture_main,
                           min_date=min_date,
                           max_date=today,
                           today=today)


@app.route('/all_devices')
def all_devices():
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT d.device_id, ml.name as model, c.name as category, m.name as manufacturer
            FROM devices d
            JOIN model ml ON d.model_id = ml.model_id
            JOIN categories c ON d.category_id = c.category_id
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            ORDER BY d.device_id
        """)
        devices = cur.fetchall()
    return render_template("all_devices.html", devices=devices)

@app.route('/device/<int:device_id>')
def device_detail(device_id):
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT ml.name as model, c.name as category, m.name as manufacturer, d.release_date,
                   d.current_price, d.is_waterproof, d.warranty_months, col.name as color
            FROM devices d
            JOIN model ml ON d.model_id = ml.model_id
            JOIN categories c ON d.category_id = c.category_id
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            JOIN color col ON d.color_id = col.color_id
            WHERE d.device_id = %s
        """, (device_id,))
        main = cur.fetchone()

        cur.execute("""
            SELECT r.name, r.website, dr.price, dr.in_stock, dr.last_updated
            FROM device_retailers dr
            JOIN retailers r ON dr.retailer_id = r.retailer_id
            WHERE dr.device_id = %s
        """, (device_id,))
        retailers = cur.fetchall()

        cur.execute("""
            SELECT osn.name, osys.developer, osys.latest_version, osys.release_date
            FROM devices d
            JOIN operating_systems osys ON d.os_id = osys.os_id
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
            WHERE d.device_id = %s
        """, (device_id,))
        os_info = cur.fetchone()

        cur.execute("""
            SELECT disp.diagonal_inches, disp.resolution, tm.name as matrix_type,
                   disp.refresh_rate_hz, disp.brightness_nits
            FROM displays disp
            JOIN techn_matr tm ON disp.techn_matr_id = tm.techn_matr_id
            WHERE disp.device_id = %s
        """, (device_id,))
        display = cur.fetchone()

        cur.execute("""
            SELECT pm.name as proc_model, s.processor_cores, s.ram_gb, s.storage_gb, st.name as storage_type
            FROM specifications s
            JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
            JOIN storage_type st ON s.storage_type_id = st.storage_type_id
            WHERE s.device_id = %s
        """, (device_id,))
        specs = cur.fetchone()

        cur.execute("""
            SELECT capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours
            FROM batteries
            WHERE device_id = %s
        """, (device_id,))
        battery = cur.fetchone()

        cur.execute("""
            SELECT megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
            FROM cameras
            WHERE device_id = %s
        """, (device_id,))
        camera = cur.fetchone()

    if request.args.get('added'):
        flash('Устройство добавлено', 'success')

    return render_template("device_detail.html",
                           device_id=device_id,
                           main=main,
                           retailers=retailers,
                           os_info=os_info,
                           display=display,
                           specs=specs,
                           battery=battery,
                           camera=camera)

@app.route('/tables_list')
def tables_list():
    with get_conn() as conn:
        tables = list_user_tables(conn)
    if not (current_user.is_authenticated and current_user.username == SUPERADMIN_USERNAME):
        tables = [t for t in tables if t.lower() != 'users']
    return render_template('table_list.html', tables=tables)

@app.route('/table/<table_name>')
def table_view(table_name):
    if table_name.lower() == 'users' and (not current_user.is_authenticated or current_user.username != SUPERADMIN_USERNAME):
        return redirect(url_for('tables_list'))
    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute(f"SELECT * FROM {table_name}")
        rows = cur.fetchall()
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table_name,))
        columns = [r[0] for r in cur.fetchall()]
        pk_name = get_pk_name(conn, table_name) or (columns[0] if columns else None)

        # 2) Затем выбираем строки с нужной сортировкой
        t = table_name.lower()
        if t == 'devices':
            order_by = 'device_id'     # Явно по device_id
        else:
            order_by = pk_name         # Для прочих таблиц — по PK (если есть)

        if order_by:
            cur.execute(f"SELECT * FROM {table_name} ORDER BY {order_by}")
        else:
            cur.execute(f"SELECT * FROM {table_name}")
        rows = cur.fetchall()

    is_dictionary = table_name in {
        'categories','manufacturers','retailers','color', 'model',
        'country','os_name','proc_model','storage_type','techn_matr'
    }
    return render_template('table_view.html',
                           table=table_name,
                           columns=columns,
                           rows=rows,
                           pk_name=pk_name,
                           is_dictionary=is_dictionary)

@app.route('/add/<table_name>', methods=['GET', 'POST'])
@admin_required
def add_row(table_name):
    next_url = request.args.get('next') or request.form.get('next_url') or ''
    embedded = request.form.get('embedded') or request.args.get('embedded')

    if request.method == 'GET' and table_name.lower() == "devices":
        dest = url_for('add_device')
        if embedded: dest += ('&' if '?' in dest else '?') + 'embedded=1'
        return redirect(dest)

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table_name,))
        columns = [r[0] for r in cur.fetchall()]
        pk_name = get_pk_name(conn, table_name) or (columns[0] if columns else None)

    if request.method == 'POST':
        with get_conn() as conn:
            cur = tup_cur(conn)

            insert_cols = []
            insert_vals = []

            # если есть явный PK — выдаём новый id и вставляем его первым
            if pk_name and pk_name.endswith('_id'):
                new_pk, _ = next_id(conn, table_name)
                insert_cols.append(pk_name)
                insert_vals.append(new_pk)

            # остальные поля — по данным формы (в порядке columns, кроме pk)
            for col in columns:
                if col == pk_name:
                    continue
                insert_cols.append(col)
                insert_vals.append(request.form.get(col))

            placeholders = ','.join(['%s'] * len(insert_cols))
            cur.execute(
                f'INSERT INTO {table_name} ({",".join(insert_cols)}) VALUES ({placeholders})',
                insert_vals
            )
            conn.commit()

        flash('Запись добавлена!', 'success')

        if next_url:
            if embedded and 'embedded=' not in next_url:
                next_url += ('&' if '?' in next_url else '?') + 'embedded=1'
            return redirect(next_url)

        dest = url_for('add_row', table_name=table_name)
        if embedded:
            dest += ('&' if '?' in dest else '?') + 'embedded=1'
        return redirect(dest)

    # GET: рендерим форму
    # скрываем pk из формы (оно заполняется автоматически)
    form_cols = [c for c in columns if c != (pk_name or '')]
    return render_template(
        'add_form.html',
        table=table_name,
        columns=form_cols,
        next_url=next_url
    )


@app.route('/delete/<table_name>/<pk>', methods=['POST'])
@admin_required
def delete_row(table_name, pk):
    t = table_name.lower()
    if t == 'users':
        try:
            target_id = int(pk)
        except ValueError:
            flash('Некорректный идентификатор пользователя.', 'danger')
            return redirect(url_for('table_view', table_name=table_name))

        if current_user.is_authenticated and int(current_user.id) == target_id:
            flash('Нельзя удалить самого себя.', 'warning')
            return redirect(url_for('table_view', table_name=table_name))

        if current_user.username != SUPERADMIN_USERNAME:
            flash('Удалять пользователей может только admin.', 'danger')
            return redirect(url_for('table_view', table_name='users'))

    if t in PROTECTED_CHILD_TABLES:
        flash('Удаление записей из этой таблицы доступно только через удаление устройства.', 'warning')
        return redirect(url_for('table_view', table_name=table_name))

    if table_name == 'devices':
        return redirect(url_for('delete_device', device_id=pk))

    with get_conn() as conn:
        pk_name = get_pk_name(conn, table_name) or f"{table_name.rstrip('s')}_id"
        in_use, where = value_in_use(conn, table_name, pk)
        if in_use:
            flash(f"Нельзя удалить: значение используется ({where}).", "danger")
            return redirect(url_for('table_view', table_name=table_name))

        cur = tup_cur(conn)
        cur.execute(f"DELETE FROM {table_name} WHERE {pk_name} = %s", (pk,))
        conn.commit()

    flash("Удалено", "success")
    return redirect(url_for('table_view', table_name=table_name))

@app.route('/delete_device/<int:device_id>', methods=['POST'])
@admin_required
def delete_device(device_id):
    with get_conn() as conn:
        cur = tup_cur(conn)
        # если в схеме есть ON DELETE CASCADE, можно удалить только из devices
        cur.execute("DELETE FROM displays WHERE device_id=%s", (device_id,))
        cur.execute("DELETE FROM specifications WHERE device_id=%s", (device_id,))
        cur.execute("DELETE FROM cameras WHERE device_id=%s", (device_id,))
        cur.execute("DELETE FROM batteries WHERE device_id=%s", (device_id,))
        cur.execute("DELETE FROM device_retailers WHERE device_id=%s", (device_id,))
        cur.execute("DELETE FROM devices WHERE device_id=%s", (device_id,))
        conn.commit()
    flash("Устройство и все связанные данные удалены", "success")
    return redirect(url_for('table_view', table_name='devices'))

# -------------------------------------------------
# Статистика
# -------------------------------------------------
@app.route('/statistic', methods=['GET', 'POST'])
def statistic():
    info_by = request.form.get('info_by') or request.args.get('info_by') or 'category'
    is_admin = current_user.is_authenticated and getattr(current_user, 'is_admin', False)

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT MIN(current_price), AVG(current_price), MAX(current_price)
            FROM devices
            WHERE current_price IS NOT NULL
        """)
        min_p, avg_p, max_p = cur.fetchone()
        min_price = int(min_p or 0)
        avg_price = int(avg_p or 0)
        max_price = int(max_p or 0)

        cur.execute("""
            SELECT d.device_id, ml.name AS model, d.current_price
            FROM devices d
            JOIN model ml ON d.model_id = ml.model_id
            WHERE current_price IS NOT NULL
            ORDER BY current_price ASC
            LIMIT 5
        """)
        cheapest = cur.fetchall()

        cur.execute("""
            SELECT d.device_id, ml.name AS model, d.current_price
            FROM devices d
            JOIN model ml ON d.model_id = ml.model_id
            WHERE current_price IS NOT NULL
            ORDER BY current_price DESC
            LIMIT 5
        """)
        expensive = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*)
            FROM devices d
            LEFT JOIN specifications s ON s.device_id = d.device_id
            WHERE s.device_id IS NULL
        """)
        devices_without_specs = cur.fetchone()[0] or 0

        try:
            cur.execute("SELECT COUNT(*) FROM devices WHERE COALESCE(is_waterproof, FALSE) = FALSE")
            devices_without_waterproof = cur.fetchone()[0] or 0
        except:
            devices_without_waterproof = 0

        purpose_map = {
            'devices': 'главная',
            'categories': 'справочник',
            'manufacturers': 'справочник',
            'retailers': 'справочник',
            'color': 'справочник',
            'country': 'справочник',
            'os_name': 'справочник',
            'proc_model': 'справочник',
            'storage_type': 'справочник',
            'techn_matr': 'справочник',
            'model' : 'справочник',
            'device_retailers': 'дополнительная',
            'batteries': 'дополнительная',
            'cameras': 'дополнительная',
            'displays': 'дополнительная',
            'specifications': 'дополнительная',
            'operating_systems': 'дополнительная',
            'users': 'дополнительная'
        }
        rus_desc = {
            'batteries': 'Характеристики батареи',
            'cameras': 'Характеристики камер',
            'categories': 'Справочник категорий',
            'color': 'Справочник цветов',
            'country': 'Справочник стран',
            'device_retailers': 'Цены/наличие у продавцов',
            'devices': 'Устройства (основная)',
            'displays': 'Характеристики дисплея',
            'manufacturers': 'Справочник производителей',
            'operating_systems': 'Версии ОС для устройств',
            'os_name': 'Справочник названий ОС',
            'proc_model': 'Модели процессоров',
            'retailers': 'Справочник продавцов',
            'specifications': 'Прочие характеристики',
            'storage_type': 'Типы накопителей',
            'techn_matr': 'Типы матрицы дисплея',
            'model' : 'Справочник моделей устройств',
            'users': 'Пользователи системы'
        }

        table_names = list_user_tables(conn)
        if not is_admin:
            table_names = [t for t in table_names if t.lower() != 'users']

        tables_info = []
        for name in table_names:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                rows = cur.fetchone()[0] or 0
            except Exception:
                rows = 0
            cols = count_columns(conn, name)
            tables_info.append({
                'name': name,
                'rus': rus_desc.get(name, '—'),
                'purpose': purpose_map.get(name, 'дополнительная'),
                'rows': rows,
                'columns': cols
            })
        order = {'главная': 0, 'справочник': 1, 'дополнительная': 2}
        tables_info.sort(key=lambda x: (order.get(x['purpose'], 99), x['name']))

        # категории
        cur.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(CASE WHEN in_stock THEN 1 ELSE 0 END) AS in_stock_any
              FROM device_retailers
              GROUP BY device_id
            )
            SELECT c.name,
                   COUNT(d.device_id)                                           AS devices_count,
                   COUNT(CASE WHEN dr.in_stock_any=1 THEN d.device_id END)      AS in_stock_devices,
                   MIN(d.current_price), AVG(d.current_price), MAX(d.current_price)
            FROM categories c
            LEFT JOIN devices d ON d.category_id = c.category_id
            LEFT JOIN dr_any dr ON dr.device_id = d.device_id
            GROUP BY c.category_id, c.name
            HAVING COUNT(d.device_id) > 0
            ORDER BY devices_count DESC, c.name
        """)
        categories_stat = [
            (r[0], r[1] or 0, r[2] or 0,
             int(r[3]) if r[3] is not None else 0,
             int(r[4]) if r[4] is not None else 0,
             int(r[5]) if r[5] is not None else 0)
            for r in cur.fetchall()
        ]

        # производители
        cur.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(CASE WHEN in_stock THEN 1 ELSE 0 END) AS in_stock_any
              FROM device_retailers
              GROUP BY device_id
            )
            SELECT m.name,
                   COUNT(d.device_id)                                           AS devices_count,
                   COUNT(CASE WHEN dr.in_stock_any=1 THEN d.device_id END)      AS in_stock_devices,
                   MIN(d.current_price), AVG(d.current_price), MAX(d.current_price)
            FROM manufacturers m
            LEFT JOIN devices d ON d.manufacturer_id = m.manufacturer_id
            LEFT JOIN dr_any dr ON dr.device_id = d.device_id
            GROUP BY m.manufacturer_id, m.name
            HAVING COUNT(d.device_id) > 0
            ORDER BY devices_count DESC, m.name
        """)
        manufacturers_stat = [
            (r[0], r[1] or 0, r[2] or 0,
             int(r[3]) if r[3] is not None else 0,
             int(r[4]) if r[4] is not None else 0,
             int(r[5]) if r[5] is not None else 0)
            for r in cur.fetchall()
        ]

        # страны
        cur.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(CASE WHEN in_stock THEN 1 ELSE 0 END) AS in_stock_any
              FROM device_retailers
              GROUP BY device_id
            )
            SELECT co.name,
                   COUNT(d.device_id)                                           AS devices_count,
                   COUNT(CASE WHEN dr.in_stock_any=1 THEN d.device_id END)      AS in_stock_devices,
                   MIN(d.current_price), AVG(d.current_price), MAX(d.current_price)
            FROM country co
            LEFT JOIN manufacturers m ON m.country_id      = co.country_id
            LEFT JOIN devices d       ON d.manufacturer_id = m.manufacturer_id
            LEFT JOIN dr_any dr       ON dr.device_id      = d.device_id
            GROUP BY co.country_id, co.name
            HAVING COUNT(d.device_id) > 0
            ORDER BY devices_count DESC, co.name
        """)
        countries_stat = [
            (r[0], r[1] or 0, r[2] or 0,
             int(r[3]) if r[3] is not None else 0,
             int(r[4]) if r[4] is not None else 0,
             int(r[5]) if r[5] is not None else 0)
            for r in cur.fetchall()
        ]

        # продавцы
        cur.execute("""
            SELECT r.name,
                   COUNT(DISTINCT dr.device_id)                                  AS devices_count,
                   COUNT(DISTINCT CASE WHEN dr.in_stock THEN dr.device_id END)   AS in_stock_devices,
                   MIN(dr.price), AVG(dr.price), MAX(dr.price)
            FROM retailers r
            LEFT JOIN device_retailers dr ON dr.retailer_id = r.retailer_id
            GROUP BY r.retailer_id, r.name
            HAVING COUNT(DISTINCT dr.device_id) > 0
            ORDER BY devices_count DESC, r.name
        """)
        retailers_stat = [
            (r[0], r[1] or 0, r[2] or 0,
             int(r[3]) if r[3] is not None else 0,
             int(r[4]) if r[4] is not None else 0,
             int(r[5]) if r[5] is not None else 0)
            for r in cur.fetchall()
        ]

        if info_by == 'manufacturer':
            info_results = manufacturers_stat
        elif info_by == 'retailer':
            info_results = retailers_stat
        elif info_by == 'country':
            info_results = countries_stat
        else:
            info_by = 'category'
            info_results = categories_stat

        # Линии цен для топ-4 категорий
        cur.execute("""
            SELECT c.category_id, c.name, COUNT(d.device_id) AS cnt
            FROM categories c
            JOIN devices d ON d.category_id = c.category_id AND d.current_price IS NOT NULL
            GROUP BY c.category_id, c.name
            ORDER BY cnt DESC, c.name
            LIMIT 4
        """)
        top4 = cur.fetchall()

        price_lines = []
        max_series_len = 0
        max_series_price = 0
        for cat_id, cat_name, _ in top4:
            cur.execute("""
                SELECT current_price
                FROM devices
                WHERE category_id = %s AND current_price IS NOT NULL
                ORDER BY current_price ASC
            """, (cat_id,))
            prices = [int(r[0]) for r in cur.fetchall()][:60]
            if prices:
                price_lines.append({'name': cat_name, 'prices': prices})
                max_series_len = max(max_series_len, len(prices))
                max_series_price = max(max_series_price, max(prices))

    return render_template(
        'statistic.html',
        info_by=info_by,
        info_results=info_results,
        min_price=min_price, avg_price=avg_price, max_price=max_price,
        cheapest=cheapest, expensive=expensive,
        devices_without_specs=devices_without_specs,
        devices_without_waterproof=devices_without_waterproof,
        top_cat=[(r[0], r[1]) for r in categories_stat[:5]],
        price_lines=price_lines,
        price_lines_max_x=max_series_len if max_series_len else 1,
        price_lines_max_y=max_series_price if max_series_price else 1,
        tables_info=tables_info
    )

# -------------------------------------------------
# Поиск
# -------------------------------------------------
@app.route('/search', methods=['GET', 'POST'])
def search():
    mode = request.form.get('mode', 'by1')
    results = None
    selected_manufacturer = request.form.get('manufacturer_id') if request.method == 'POST' else ''
    selected_country      = request.form.get('country_id')      if request.method == 'POST' else ''
    selected_color        = request.form.get('color_id')        if request.method == 'POST' else ''

    base_select = '''
        SELECT DISTINCT
            d.device_id,
            ml.name as model,
            m.name  AS manufacturer,
            c.name  AS category,
            col.name AS color,
            co.name AS country,
            st.name AS storage_type,
            pm.name AS proc_model,
            tm.name AS techn_matr,
            d.current_price
        FROM devices d
        JOIN model ml ON d.model_id = ml.model_id
        JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
        JOIN categories    c ON d.category_id     = c.category_id
        JOIN color       col ON d.color_id        = col.color_id
        LEFT JOIN specifications s  ON d.device_id    = s.device_id
        LEFT JOIN storage_type   st ON s.storage_type_id = st.storage_type_id
        LEFT JOIN proc_model     pm ON s.proc_model_id   = pm.proc_model_id
        LEFT JOIN displays     disp ON d.device_id       = disp.device_id
        LEFT JOIN techn_matr     tm ON disp.techn_matr_id = tm.techn_matr_id
        LEFT JOIN country        co ON m.country_id       = co.country_id
        WHERE 1=1
    '''

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("""
            SELECT DISTINCT m.manufacturer_id, m.name
            FROM manufacturers m
            JOIN devices d ON d.manufacturer_id = m.manufacturer_id
        """)
        manufacturers = _sort_ci_tuples(cur.fetchall())

        cur.execute("""
            SELECT DISTINCT co.country_id, co.name
            FROM country co
            JOIN manufacturers m ON m.country_id = co.country_id
            JOIN devices d ON d.manufacturer_id = m.manufacturer_id
        """)
        countries = _sort_ci_tuples(cur.fetchall())

        if request.method == 'POST':
            if mode == 'by1' and selected_manufacturer:
                inner = base_select + ' AND d.manufacturer_id = %s'
                q = f"SELECT * FROM ({inner}) AS t ORDER BY LOWER(model)"
                cur.execute(q, (selected_manufacturer,))
                results = cur.fetchall()

            elif mode == 'by2' and selected_country and selected_color:
                inner = base_select + ' AND m.country_id = %s AND d.color_id = %s'
                q = f"SELECT * FROM ({inner}) AS t ORDER BY LOWER(model)"
                cur.execute(q, (selected_country, selected_color))
                results = cur.fetchall()

    return render_template('search.html',
        mode=mode,
        manufacturers=manufacturers,
        countries=countries,
        selected_manufacturer=selected_manufacturer,
        selected_country=selected_country,
        selected_color=selected_color,
        results=results
    )

@app.route('/api/attribute_values')
def api_attribute_values():
    attr = request.args.get('attr')
    other_attr = request.args.get('other_attr')
    other_val = request.args.get('other_val')

    sql_map = {
        'category':      ('categories', 'category_id', 'name'),
        'manufacturer':  ('manufacturers', 'manufacturer_id', 'name'),
        'color':         ('color', 'color_id', 'name'),
        'storage_type':  ('storage_type', 'storage_type_id', 'name'),
        'country':       ('country', 'country_id', 'name'),
        'retailer':      ('retailers', 'retailer_id', 'name'),
    }
    if attr not in sql_map:
        return jsonify([])

    table, id_field, name_field = sql_map[attr]
    query = f"SELECT DISTINCT t.{id_field}, t.{name_field} FROM {table} t "
    params: List[Any] = []

    if attr == 'storage_type':
        query += """
          JOIN specifications s ON t.storage_type_id = s.storage_type_id
          JOIN devices d ON s.device_id = d.device_id
        """
    elif attr == 'country':
        query += """
          JOIN manufacturers m ON t.country_id = m.country_id
          JOIN devices d ON m.manufacturer_id = d.manufacturer_id
        """
    elif attr == 'retailer':
        query += """
          LEFT JOIN device_retailers dr ON t.retailer_id = dr.retailer_id
          JOIN devices d ON dr.device_id = d.device_id
        """
    else:
        query += f"JOIN devices d ON d.{id_field} = t.{id_field} "

    if other_attr in sql_map and other_val:
        other_table, other_id_field, _ = sql_map[other_attr]
        if other_attr == 'storage_type':
            query += "LEFT JOIN specifications s2 ON d.device_id = s2.device_id AND s2.storage_type_id = %s "
        elif other_attr == 'country':
            query += "JOIN manufacturers m2 ON d.manufacturer_id = m2.manufacturer_id AND m2.country_id = %s "
        elif other_attr == 'retailer':
            query += "LEFT JOIN device_retailers dr2 ON d.device_id = dr2.device_id AND dr2.retailer_id = %s "
        else:
            query += f"AND d.{other_id_field} = %s "
        params.append(other_val)

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute(query, params)
        data = [{'id': row[0], 'name': row[1]} for row in cur.fetchall()]
        data = _sort_ci_dicts(data, 'name')
    return jsonify(data)

@app.route('/api/category_price_range')
def api_category_price_range():
    category_id = request.args.get('category_id')
    with get_conn() as conn:
        cur = tup_cur(conn)
        if category_id and category_id != "all":
            cur.execute("SELECT MIN(current_price), MAX(current_price) FROM devices WHERE category_id = %s", (category_id,))
        else:
            cur.execute("SELECT MIN(current_price), MAX(current_price) FROM devices")
        row = cur.fetchone()
    min_price = int(row[0] or 0)
    max_price = int(row[1] or 0)
    return {'min': min_price, 'max': max_price}

@app.route('/api/auto_search')
def api_auto_search():
    category_id = request.args.get('category_id', "all")
    manufacturer_id = request.args.get('manufacturer_id', "all")
    color_id = request.args.get('color_id', "all")

    query = '''
        SELECT DISTINCT
            d.device_id,
            ml.name AS model,
            m.name  AS manufacturer,
            c.name  AS category,
            col.name AS color,
            co.name AS country,
            st.name AS storage_type,
            pm.name AS proc_model,
            tm.name AS techn_matr,
            d.current_price
        FROM devices d
        JOIN model ml ON model_id = ml.model_id
        JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
        JOIN categories    c ON d.category_id     = c.category_id
        JOIN color       col ON d.color_id        = col.color_id
        LEFT JOIN specifications s  ON d.device_id    = s.device_id
        LEFT JOIN storage_type   st ON s.storage_type_id = st.storage_type_id
        LEFT JOIN proc_model     pm ON s.proc_model_id   = pm.proc_model_id
        LEFT JOIN displays     disp ON d.device_id       = disp.device_id
        LEFT JOIN techn_matr     tm ON disp.techn_matr_id = tm.techn_matr_id
        LEFT JOIN country        co ON m.country_id       = co.country_id
        WHERE 1=1
    '''
    params: List[Any] = []
    if category_id != "all":
        query += " AND d.category_id = %s"
        params.append(category_id)
    if manufacturer_id != "all":
        query += " AND d.manufacturer_id = %s"
        params.append(manufacturer_id)
    if color_id != "all":
        query += " AND d.color_id = %s"
        params.append(color_id)

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute(query + " ORDER BY LOWER(d.model)", params)
        results = cur.fetchall()
    return render_template('search_results_table.html', results=results)

@app.route('/api/filter_options')
def api_filter_options():
    category_id = request.args.get('category_id')
    manufacturer_id = request.args.get('manufacturer_id')

    with get_conn() as conn:
        cur = tup_cur(conn)
        q = """
            SELECT DISTINCT m.manufacturer_id, m.name
            FROM manufacturers m
            JOIN devices d ON m.manufacturer_id = d.manufacturer_id
            WHERE 1=1
        """
        params: List[Any] = []
        if category_id and category_id != "all":
            q += " AND d.category_id = %s"
            params.append(category_id)
        cur.execute(q, params)
        manufacturers = [{'manufacturer_id': row[0], 'name': row[1]} for row in cur.fetchall()]
        manufacturers = _sort_ci_dicts(manufacturers, 'name')

        q = """
            SELECT DISTINCT col.color_id, col.name
            FROM color col
            JOIN devices d ON col.color_id = d.color_id
            WHERE 1=1
        """
        params = []
        if category_id and category_id != "all":
            q += " AND d.category_id = %s"
            params.append(category_id)
        if manufacturer_id and manufacturer_id != "all":
            q += " AND d.manufacturer_id = %s"
            params.append(manufacturer_id)
        cur.execute(q, params)
        colors = [{'color_id': row[0], 'name': row[1]} for row in cur.fetchall()]
        colors = _sort_ci_dicts(colors, 'name')

    return jsonify({'manufacturers': manufacturers, 'colors': colors})

# -------------------------------------------------
# Справочники / ОС / продавцы
# -------------------------------------------------
@app.route('/add_manufacturer', methods=['GET', 'POST'])
@admin_required
def add_manufacturer():
    next_url = request.args.get('next')
    message = ""
    if request.method == 'POST':
        name = request.form.get('name')
        country_id = request.form.get('country_id')
        foundation_year = request.form.get('foundation_year')
        website = request.form.get('website')
        url_pattern = re.compile(r'^https?://[^\s]+$')
        if not url_pattern.match(website):
            flash('Некорректная ссылка на сайт. Введите адрес в формате https://example.com', 'danger')
            return redirect(url_for('add_manufacturer'))

        with get_conn() as conn:
            cur = tup_cur(conn)
            new_id, _ = next_id(conn, 'manufacturers')
            cur.execute("""
                INSERT INTO manufacturers (manufacturer_id, name, country_id, foundation_year, website)
                VALUES (%s, %s, %s, %s, %s)
            """, (new_id, name, country_id, foundation_year, website))
            conn.commit()

        next_url = request.form.get('next_url')
        flash('Производитель добавлен!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_manufacturer'))

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("SELECT country_id, name FROM country")
        countries = _sort_ci_tuples(cur.fetchall())
    return render_template('add_manufacturer.html', countries=countries, message=message, next_url=next_url)

@app.route('/add_category', methods=['GET', 'POST'])
@admin_required
def add_category():
    next_url = request.args.get('next')
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        with get_conn() as conn:
            cur = tup_cur(conn)
            new_id, _ = next_id(conn, 'categories')
            cur.execute("INSERT INTO categories (category_id, name, description) VALUES (%s, %s, %s)", (new_id, name, description))
            conn.commit()
        next_url = request.form.get('next_url')
        flash('Категория добавлена!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_category'))
    return render_template('add_category.html', next_url=next_url)

@app.route('/add_os', methods=['GET', 'POST'])
@admin_required
def add_os():
    today = _Date.today().isoformat()
    next_url = request.args.get('next')

    with get_conn() as conn:
        cur = tup_cur(conn)
        cur.execute("SELECT os_name_id, name FROM os_name")
        os_names = _sort_ci_tuples(cur.fetchall())

    if request.method == 'POST':
        os_name_id = request.form.get('os_name_id')
        developer = request.form.get('developer')
        latest_version = request.form.get('latest_version')
        release_date = request.form.get('release_date')
        with get_conn() as conn:
            cur = tup_cur(conn)
            new_id, _ = next_id(conn, 'operating_systems')
            cur.execute("""
                INSERT INTO operating_systems (os_id, os_name_id, developer, latest_version, release_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (new_id, os_name_id, developer, latest_version, release_date))
            conn.commit()
        flash('Операционная система добавлена!', 'success')
        if request.form.get('next_url'):
            return redirect(request.form['next_url'])
        return redirect(url_for('add_os'))
    return render_template('add_os.html', os_names=os_names, today=today, next_url=next_url)

@app.route('/add_retailer', methods=['GET', 'POST'])
@admin_required
def add_retailer():
    next_url = request.args.get('next')
    if request.method == 'POST':
        name = request.form.get('name')
        website = request.form.get('website')
        rating = request.form.get('rating')
        try:
            rating_val = float(rating)
            if not (0.1 <= rating_val <= 5.0):
                raise ValueError
        except:
            flash("Рейтинг должен быть от 0.1 до 5.0", "danger")
            return redirect(request.url)
        with get_conn() as conn:
            cur = tup_cur(conn)
            new_id, _ = next_id(conn, 'retailers')
            cur.execute("INSERT INTO retailers (retailer_id, name, website, rating) VALUES (%s, %s, %s, %s)", (new_id, name, website, rating))
            conn.commit()
        next_url = request.form.get('next_url')
        flash('Продавец добавлен!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_retailer'))
    return render_template('add_retailer.html', next_url=next_url)



#для дозаполнения
@app.route('/api/model_prefill', endpoint='api_model_prefill')
@login_required
def api_model_prefill():
    """Возвращает экстры последнего (прошлого) устройства той же модели, исключая текущий device_id."""
    device_id = request.args.get('device_id', type=int)
    model_id = request.args.get('model_id', type=int)

    if not (device_id or model_id):
        return jsonify({'ok': False, 'reason': 'missing_ids'}), 400

    with get_conn() as conn:
        cur = tup_cur(conn)

        # Если model_id не передали — выясним его по текущему устройству
        if not model_id:
            cur.execute("SELECT model_id FROM devices WHERE device_id=%s", (device_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'ok': False, 'reason': 'device_not_found'}), 404
            model_id = row[0]

        # Берём последний device этой модели, но не текущий
        params = [model_id]
        sql = "SELECT device_id FROM devices WHERE model_id=%s"
        if device_id:
            sql += " AND device_id <> %s"
            params.append(device_id)
        sql += " ORDER BY device_id DESC LIMIT 1"

        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': True, 'source_device_id': None, 'scalars': {}})

        src_dev = row[0]
        scalars = {}

        # specifications
        cur.execute("""
            SELECT proc_model_id, processor_cores, ram_gb, storage_gb, storage_type_id
            FROM specifications WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'proc_model_id': r[0], 'processor_cores': r[1],
                'ram_gb': r[2], 'storage_gb': r[3], 'storage_type_id': r[4],
            })

        # displays
        cur.execute("""
            SELECT diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits
            FROM displays WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'diagonal_inches': r[0], 'resolution': r[1], 'techn_matr_id': r[2],
                'refresh_rate_hz': r[3], 'brightness_nits': r[4],
            })

        # cameras
        cur.execute("""
            SELECT megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
            FROM cameras WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'megapixels_main': r[0], 'aperture_main': r[1], 'optical_zoom_x': r[2],
                'video_resolution': r[3], 'has_ai_enhance': bool(r[4]),
            })

        # batteries
        cur.execute("""
            SELECT capacity_mah, fast_charging_w, estimated_life_hours, wireless_charging
            FROM batteries WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'capacity_mah': r[0], 'fast_charging_w': r[1],
                'estimated_life_hours': r[2], 'wireless_charging': bool(r[3]),
            })

    return jsonify({'ok': True, 'source_device_id': src_dev, 'scalars': scalars})

# -------------------------------------------------
# Доп. характеристики (edit_extras)
# -------------------------------------------------

@app.route('/device/<int:device_id>/extras', methods=['GET', 'POST'])
@admin_required
def edit_extras(device_id):
    embedded_flag = request.args.get('embedded') or request.form.get('embedded')
    today = _Date.today().isoformat()

    def back_to_extras():
        if embedded_flag:
            return redirect(url_for('edit_extras', device_id=device_id, embedded=1))
        return redirect(url_for('edit_extras', device_id=device_id))

    with get_conn() as conn:
        cur = tup_cur(conn)

        cur.execute("""
            SELECT aperture_main
            FROM cameras
            WHERE aperture_main IS NOT NULL
            GROUP BY aperture_main
            ORDER BY LOWER(aperture_main)
        """)
        aperture_main = [row[0] for row in cur.fetchall() if row[0]]

        cur.execute("SELECT proc_model_id, name FROM proc_model")
        proc_models = _sort_ci_tuples(cur.fetchall())

        cur.execute("SELECT storage_type_id, name FROM storage_type")
        storage_types = _sort_ci_tuples(cur.fetchall())

        cur.execute("SELECT techn_matr_id, name FROM techn_matr")
        techn_matrices = _sort_ci_tuples(cur.fetchall())

        cur.execute("SELECT * FROM specifications WHERE device_id=%s", (device_id,))
        spec = cur.fetchone()

        cur.execute("SELECT * FROM displays WHERE device_id=%s", (device_id,))
        display = cur.fetchone()

        cur.execute("SELECT * FROM cameras WHERE device_id=%s", (device_id,))
        camera = cur.fetchone()

        cur.execute("SELECT * FROM batteries WHERE device_id=%s", (device_id,))
        battery = cur.fetchone()

        cur.execute("SELECT retailer_id, name FROM retailers")
        retailers = _sort_ci_tuples(cur.fetchall())

        cur.execute("""
            SELECT dr.device_retailer_id, dr.retailer_id, r.name, dr.price, dr.in_stock, dr.last_updated
            FROM device_retailers dr
            JOIN retailers r ON r.retailer_id = dr.retailer_id
            WHERE dr.device_id=%s
            ORDER BY LOWER(r.name)
        """, (device_id,))
        offers = cur.fetchall()

    if request.method == 'POST':
        tab = request.form.get('tab')

        if tab == 'specification':
            data: Dict[str, Any] = {}
            proc_model_id = request.form.get('proc_model_id')
            if proc_model_id:
                data['proc_model_id'] = int(proc_model_id)

            def _int_in(name, lo, hi, store):
                val = request.form.get(name)
                if not val:
                    return True
                try:
                    iv = int(val)
                    if not (lo <= iv <= hi):
                        raise ValueError
                except:
                    flash(f'{name}: {lo}-{hi}', 'danger'); return False
                store[name] = iv; return True

            if not _int_in('processor_cores', 1, 20, data): return back_to_extras()
            if not _int_in('ram_gb', 1, 32, data): return back_to_extras()
            if not _int_in('storage_gb', 1, 2048, data): return back_to_extras()

            storage_type_id = request.form.get('storage_type_id')
            if storage_type_id:
                data['storage_type_id'] = int(storage_type_id)

            if data:
                with get_conn() as conn:
                    cur = tup_cur(conn)
                    new_id1, _ = next_id(conn, 'specifications')
                    cur.execute("SELECT spec_id FROM specifications WHERE device_id=%s", (device_id,))
                    exists = cur.fetchone()
                    if exists:
                        sets = ", ".join([f"{k}=%s" for k in data])
                        cur.execute(f"UPDATE specifications SET {sets} WHERE device_id=%s",
                                    (*data.values(), device_id))
                    else:
                        fields = ", ".join(['spec_id'] + ['device_id'] + list(data.keys()))
                        ph = ", ".join(['%s'] * (len(data) + 2))
                        cur.execute(f"INSERT INTO specifications ({fields}) VALUES ({ph})",
                                    (new_id1, device_id, *data.values()))
                    conn.commit()
                flash("Спецификация обновлена", "success")

        elif tab == 'display':
            try:
                diagonal_inches = float(request.form.get('diagonal_inches'))
                if not (1.0 <= diagonal_inches <= 100.0): raise ValueError
                resolution = request.form.get('resolution')
                if not resolution or not (7 <= len(resolution) <= 11):
                    raise ValueError
                techn_matr_id = int(request.form.get('techn_matr_id'))
                refresh_rate_hz = int(request.form.get('refresh_rate_hz'))
                if not (1 <= refresh_rate_hz <= 360): raise ValueError
                brightness_nits = int(request.form.get('brightness_nits'))
                if not (1 <= brightness_nits <= 10000): raise ValueError
            except:
                flash('Проверьте поля дисплея (диапазоны и формат).', 'danger'); return back_to_extras()

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_id2, _ = next_id(conn, 'displays')
                cur.execute("SELECT display_id FROM displays WHERE device_id=%s", (device_id,))
                exists = cur.fetchone()
                if exists:
                    cur.execute("""
                        UPDATE displays
                        SET diagonal_inches=%s, resolution=%s, techn_matr_id=%s,
                            refresh_rate_hz=%s, brightness_nits=%s
                        WHERE device_id=%s
                    """, (diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits, device_id))
                else:
                    cur.execute("""
                        INSERT INTO displays (display_id, device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (new_id2, device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits))
                conn.commit()
            flash("Дисплей обновлён", "success")

        elif tab == 'camera':
            try:
                megapixels_main = float(request.form.get('megapixels_main'))
                if not (2.0 <= megapixels_main <= 200.0): raise ValueError
                aperture_main = request.form.get('aperture_main')
                if not aperture_main or not (3 <= len(aperture_main) <= 6): raise ValueError
                optical_zoom_x = float(request.form.get('optical_zoom_x'))
                if not (0.0 <= optical_zoom_x <= 144.0): raise ValueError
                video_resolution = request.form.get('video_resolution')
                if not video_resolution or not (7 <= len(video_resolution) <= 11): raise ValueError
                has_ai_enhance = True if request.form.get('has_ai_enhance') else False
            except:
                flash('Проверьте поля камеры (диапазоны и формат).', 'danger'); return back_to_extras()

            with get_conn() as conn:
                cur = tup_cur(conn)
                cur.execute("SELECT camera_id FROM cameras WHERE device_id=%s", (device_id,))
                exists = cur.fetchone()
                if exists:
                    cur.execute("""
                        UPDATE cameras
                        SET megapixels_main=%s, aperture_main=%s, optical_zoom_x=%s,
                            video_resolution=%s, has_ai_enhance=%s
                        WHERE device_id=%s
                    """, (megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance, device_id))
                else:
                    new_id3, _ = next_id(conn, 'cameras')
                    cur.execute("""
                        INSERT INTO cameras (camera_id, device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance)
                        VALUES (%s,%s,%s,%s,%s,%s, %s)
                    """, (new_id3, device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance))
                conn.commit()
            flash("Камера обновлена", "success")

        elif tab == 'battery':
            try:
                capacity_mah = int(request.form.get('capacity_mah'))
                if not (1 <= capacity_mah <= 20000): raise ValueError
                fast_charging_w = float(request.form.get('fast_charging_w'))
                if not (0.0 <= fast_charging_w <= 20.0): raise ValueError
                estimated_life_hours = float(request.form.get('estimated_life_hours'))
                if not (0.0 <= estimated_life_hours <= 96.0): raise ValueError
                wireless_charging = True if request.form.get('wireless_charging') else False
            except:
                flash('Проверьте поля батареи (диапазоны).', 'danger'); return back_to_extras()

            with get_conn() as conn:
                cur = tup_cur(conn)
                new_id4, _ = next_id(conn, 'batteries')
                cur.execute("SELECT battery_id FROM batteries WHERE device_id=%s", (device_id,))
                exists = cur.fetchone()
                if exists:
                    cur.execute("""
                        UPDATE batteries
                        SET capacity_mah=%s, fast_charging_w=%s, wireless_charging=%s, estimated_life_hours=%s
                        WHERE device_id=%s
                    """, (capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours, device_id))
                else:
                    cur.execute("""
                        INSERT INTO batteries (battery_id, device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, (new_id4, device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours))
                conn.commit()
            flash("Батарея обновлена", "success")

        elif tab == 'offers':
            action = request.form.get('action') or 'add_offer'
            if action == 'add_offer':
                retailer_id  = request.form.get('retailer_id')
                site_price   = request.form.get('site_price')
                in_stock     = True if request.form.get('in_stock') == 'on' else False
                last_updated = request.form.get('last_updated') or today

                try:
                    price_val = float(site_price)
                    if not (0.0 <= price_val <= 1_000_000.0): raise ValueError
                    _DT.strptime(last_updated, "%Y-%m-%d")
                except:
                    flash('Проверьте цену (0–1 000 000) и дату (YYYY-MM-DD).', 'danger'); return back_to_extras()

                with get_conn() as conn:
                    cur = tup_cur(conn)
                    new_id5, _ = next_id(conn, 'device_retailers')
                    cur.execute("""
                        SELECT device_retailer_id
                        FROM device_retailers
                        WHERE device_id=%s AND retailer_id=%s
                    """, ( device_id, retailer_id))
                    row = cur.fetchone()
                    if row:
                        cur.execute("""
                            UPDATE device_retailers
                            SET price=%s, in_stock=%s, last_updated=%s
                            WHERE device_retailer_id=%s
                        """, (price_val, in_stock, last_updated, row[0]))
                        msg = 'Предложение обновлено.'
                    else:
                        cur.execute("""
                            INSERT INTO device_retailers (device_retailer_id, device_id, retailer_id, price, in_stock, last_updated)
                            VALUES (%s,%s,%s,%s,%s,%s)
                        """, (new_id5, device_id, retailer_id, price_val, in_stock, last_updated))
                        msg = 'Продавец добавлен для устройства.'
                    conn.commit()
                flash(msg, 'success')
                return back_to_extras()

            elif action == 'del_offer':
                dr_id = request.form.get('device_retailer_id')
                try:
                    dr_id = int(dr_id)
                except:
                    flash('Некорректный идентификатор предложения.', 'danger'); return back_to_extras()
                with get_conn() as conn:
                    cur = tup_cur(conn)
                    cur.execute("DELETE FROM device_retailers WHERE device_retailer_id=%s AND device_id=%s", (dr_id, device_id))
                    conn.commit()
                    if cur.rowcount and cur.rowcount > 0:
                        flash('Предложение удалено.', 'success')
                    else:
                        flash('Предложение не найдено (возможно, уже удалено).', 'warning')
                return back_to_extras()

        return back_to_extras()

    return render_template('edit_extras.html',
                           device_id=device_id,
                           proc_models=proc_models,
                           storage_types=storage_types,
                           techn_matrices=techn_matrices,
                           aperture_main=aperture_main,
                           spec=spec,
                           display=display,
                           camera=camera,
                           battery=battery,
                           retailers=retailers,
                           offers=offers,
                           today=today)


# --- НОВОЕ: API для предзаполнения доп.характеристик по последнему девайсу модели ---
@app.route('/api/last_specs')
@login_required
def api_last_specs():
    model_id = request.args.get('model_id', type=int)
    device_id = request.args.get('device_id', type=int)

    with get_conn() as conn:
        cur = tup_cur(conn)

        # Если пришёл только device_id — выясним его model_id
        if not model_id and device_id:
            cur.execute("SELECT model_id FROM devices WHERE device_id=%s", (device_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'ok': False, 'reason': 'device_not_found'}), 404
            model_id = row[0]

        if not model_id:
            return jsonify({'ok': False, 'reason': 'missing_ids'}), 400

        # Берём последний device этой модели
        cur.execute("""
            SELECT device_id
            FROM devices
            WHERE model_id=%s
            ORDER BY device_id DESC
            LIMIT 1
        """, (model_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': True, 'scalars': {}, 'source_device_id': None})
        src_dev = row[0]

        scalars = {}

        # specifications
        cur.execute("""
            SELECT proc_model_id, processor_cores, ram_gb, storage_gb, storage_type_id
            FROM specifications WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'proc_model_id': r[0], 'processor_cores': r[1],
                'ram_gb': r[2], 'storage_gb': r[3], 'storage_type_id': r[4],
            })

        # displays
        cur.execute("""
            SELECT diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits
            FROM displays WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'diagonal_inches': r[0], 'resolution': r[1], 'techn_matr_id': r[2],
                'refresh_rate_hz': r[3], 'brightness_nits': r[4],
            })

        # cameras
        cur.execute("""
            SELECT megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
            FROM cameras WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'megapixels_main': r[0], 'aperture_main': r[1], 'optical_zoom_x': r[2],
                'video_resolution': r[3], 'has_ai_enhance': bool(r[4]),
            })

        # batteries
        cur.execute("""
            SELECT capacity_mah, fast_charging_w, estimated_life_hours, wireless_charging
            FROM batteries WHERE device_id=%s
        """, (src_dev,))
        r = cur.fetchone()
        if r:
            scalars.update({
                'capacity_mah': r[0], 'fast_charging_w': r[1],
                'estimated_life_hours': r[2], 'wireless_charging': bool(r[3]),
            })

    return jsonify({'ok': True, 'source_device_id': src_dev, 'scalars': scalars})


# -------------------------------------------------
# Точка входа
# -------------------------------------------------
if __name__ == '__main__':
    #app.run(debug=True)
    app.run(host="0.0.0.0", port=5000, debug=True)
