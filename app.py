from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import sqlite3
import os
import datetime
import re
import datetime

app = Flask(__name__)
app.secret_key = 'your-unique-secret-key-1234567890'
DB_PATH = os.path.join(os.path.dirname(__file__), 'db', '2lr.db')

REFERENCE_MAP = {
    # справочники
    'categories':     [('devices',          'category_id')],
    'manufacturers':  [('devices',          'manufacturer_id')],
    'retailers':      [('device_retailers', 'retailer_id')],
    'color':          [('devices',          'color_id')],
    'country':        [('manufacturers',    'country_id')],
    'os_name':        [('operating_systems','os_name_id')],
    'proc_model':     [('specifications',   'proc_model_id')],
    'storage_type':   [('specifications',   'storage_type_id')],
    'techn_matr':     [('displays',         'techn_matr_id')],
    # не справочник, но тоже есть ссылки
    'operating_systems': [('devices', 'os_id')],
}

def get_pk_name(cursor, table_name: str) -> str:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return cursor.fetchone()[1]  # первый столбец — PK

def value_in_use(cursor, table_name: str, pk_value: str) -> tuple[bool, str]:
    """Проверяет, используется ли значение из table_name где-то ещё.
       Возвращает (True/False, 'таблица.столбец' где нашлось)."""
    refs = REFERENCE_MAP.get(table_name, [])
    if not refs:
        return (False, '')
    pk = get_pk_name(cursor, table_name)
    for ref_table, ref_col in refs:
        cursor.execute(f"SELECT 1 FROM {ref_table} WHERE {ref_col} = ? LIMIT 1", (pk_value,))
        if cursor.fetchone():
            return (True, f"{ref_table}.{ref_col}")
    return (False, '')

def safe_date(date_str, default_today=True):
    MIN_DATE = datetime.date(2016, 1, 1)
    today = datetime.date.today()
    try:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        date = today if default_today else MIN_DATE
    if date < MIN_DATE:
        date = MIN_DATE
    if date > today:
        date = today
    return date.strftime("%Y-%m-%d")


@app.route('/', methods=['GET', 'POST'])
def index():
    # --- NEW: что пришло из формы "Справка по БД"
    mode = request.form.get('mode') if request.method == 'POST' else None
    info_by = request.form.get('info_by', 'retailer') if mode == 'info' else None
    info_results = None

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        # Базовые количества по ключевым справочникам
        totals = {}
        for t in ['devices', 'categories', 'manufacturers', 'retailers', 'color', 'country', 'os_name']:
            c.execute(f"SELECT COUNT(*) FROM {t}")
            totals[t] = c.fetchone()[0]

        # Цены
        c.execute("SELECT MIN(current_price), AVG(current_price), MAX(current_price) FROM devices")
        price_min, price_avg, price_max = c.fetchone()
        price_min = int(price_min) if price_min is not None else 0
        price_avg = int(price_avg) if price_avg is not None else 0
        price_max = int(price_max) if price_max is not None else 0

        # Диапазон дат релиза устройств
        c.execute("SELECT MIN(release_date), MAX(release_date) FROM devices")
        release_min, release_max = c.fetchone()

        # Последнее обновление данных продавцов
        c.execute("SELECT MAX(last_updated) FROM device_retailers")
        last_update = c.fetchone()[0]

        # В наличии и всего предложений
        c.execute("SELECT COUNT(DISTINCT device_id) FROM device_retailers WHERE in_stock=1")
        in_stock_devices = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM device_retailers")
        offers_total = c.fetchone()[0] or 0

        # ---- СВОДКА ПО ТАБЛИЦАМ ----
        # Назначение таблиц
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
            'device_retailers': 'дополнительная',
            'batteries': 'дополнительная',
            'cameras': 'дополнительная',
            'displays': 'дополнительная',
            'specifications': 'дополнительная',
            'operating_systems': 'дополнительная',
        }

        # Русские описания «что содержит»
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
        }


        c.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        table_names = [r[0] for r in c.fetchall()]

        tables_info = []
        for name in table_names:
            try:
                c.execute(f"SELECT COUNT(*) FROM {name}")
                rows = c.fetchone()[0] or 0
            except Exception:
                rows = 0
            c.execute(f"PRAGMA table_info({name})")
            cols = len(c.fetchall())
            tables_info.append({
                'name': name,
                'rus': rus_desc.get(name, '—'),
                'purpose': purpose_map.get(name, 'дополнительная'),
                'rows': rows,
                'columns': cols
            })

        # аккуратная сортировка: главная -> справочники -> дополнительные
        order = {'главная': 0, 'справочник': 1, 'дополнительная': 2}
        tables_info.sort(key=lambda x: (order.get(x['purpose'], 99), x['name']))
        if mode == 'info':
            if info_by == 'retailer':
                c.execute('''
                    SELECT r.name, COUNT(DISTINCT dr.device_id)
                    FROM device_retailers dr
                    JOIN retailers r ON dr.retailer_id = r.retailer_id
                    GROUP BY r.name
                    ORDER BY COUNT(DISTINCT dr.device_id) DESC
                ''')
            else:  # country
                c.execute('''
                    SELECT co.name, COUNT(DISTINCT d.device_id)
                    FROM devices d
                    JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                    JOIN country co ON m.country_id = co.country_id
                    GROUP BY co.name
                    ORDER BY COUNT(DISTINCT d.device_id) DESC
                ''')
            info_results = c.fetchall()

    # Размер файла БД (может не сработать, если путь отличается)
    try:
        import os
        db_size_mb = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2)
    except Exception:
        db_size_mb = None

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
        db_size_mb=db_size_mb,
        tables_info=tables_info,
        author_email='khristoforov.volodya@gmail.com',
        author_telegram='@vladimir_hrist',
        #для "Справка по БД"
        mode=mode,
        info_by=info_by,
        info_results=info_results
    )


@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    message = ""
    today = datetime.date.today()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Получаем значения для списков
        c.execute("SELECT manufacturer_id, name FROM manufacturers ORDER BY name COLLATE NOCASE")
        manufacturers = c.fetchall()
        c.execute("SELECT category_id, name FROM categories ORDER BY name COLLATE NOCASE")
        categories = c.fetchall()
        c.execute("""
            SELECT osys.os_id, osn.name, osys.latest_version
            FROM operating_systems osys
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
            ORDER BY osn.name, osys.latest_version
        """)
        operating_systems = c.fetchall()
        c.execute("SELECT retailer_id, name FROM retailers ORDER BY name COLLATE NOCASE")
        retailers = c.fetchall()
        c.execute("SELECT color_id, name FROM color ORDER BY name COLLATE NOCASE")
        colors = c.fetchall()

    if request.method == 'POST':
        manufacturer_id = request.form.get('manufacturer_id')
        category_id = request.form.get('category_id')
        os_id = request.form.get('os_id')
        model = request.form.get('model')
        color_id = request.form.get('color_id')
        release_date = request.form.get('release_date')
        current_price = request.form.get('current_price')
        weight_grams = request.form.get('weight_grams')
        is_waterproof = 1 if request.form.get('is_waterproof') == 'on' else 0
        warranty_months = request.form.get('warranty_months')

        # Для device_retailer
        retailer_ids = request.form.getlist('retailer_id[]')
        site_prices = request.form.getlist('site_price[]')
        last_updateds = request.form.getlist('last_updated[]')
        in_stocks = []
        for i in range(len(retailer_ids)):
            in_stock_name = f"in_stock{i}"
            in_stocks.append(1 if request.form.get(in_stock_name) == 'on' else 0)

        # Проверка длин массивов продавцов
        if not (len(retailer_ids) == len(site_prices) == len(last_updateds)):
            flash("Ошибка заполнения продавцов!", "danger")
            return redirect(url_for('add_device'))

        # Валидация и обработка дат для продавцов
        for i in range(len(retailer_ids)):
            last_updated = last_updateds[i]
            # Сначала валидация диапазона (оригинальный формат HTML — YYYY-MM-DD)
            if not ("2016-01-01" <= last_updated <= today.strftime("%Y-%m-%d")):
                flash('Дата обновления у продавца должна быть c 01.01.2016 по текущий день', 'danger')
                return redirect(url_for('add_device'))
            # Преобразуем для БД в YYYY.MM.DD
            last_updateds[i] = datetime.datetime.strptime(last_updated, "%Y-%m-%d").strftime("%Y.%m.%d")

        try:
            current_price = float(current_price)
            if not (0 <= current_price <= 1000000):
                raise ValueError("current_price out of range")
            weight_grams = int(weight_grams)
            if not (1 <= weight_grams <= 1000000):
                raise ValueError("weight_grams out of range")
            warranty_months = int(warranty_months)
            if not (1 <= warranty_months <= 24):
                raise ValueError("warranty_months out of range")
            if not ("1990-01-01" <= release_date <= today.strftime("%Y-%m-%d")):
                raise ValueError("release_date out of range")
        except Exception as e:
            flash('Некорректные значения: ' + str(e), 'danger')
            return redirect(url_for('add_device'))

        # Валидация и обработка цен продавцов
        for i in range(len(retailer_ids)):
            site_price = float(site_prices[i])
            if not (0.0 <= site_price <= 1000000.0):
                flash('Цена у продавца должна быть от 0.0 до 1000000.0', 'danger')
                return redirect(url_for('add_device'))

        # Сохранение в базу
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            # Вставляем новое устройство
            c.execute("""
                INSERT INTO devices (manufacturer_id, category_id, os_id, model, release_date, current_price, weight_grams, color_id, is_waterproof, warranty_months)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (manufacturer_id, category_id, os_id, model, release_date, current_price, weight_grams, color_id, is_waterproof, warranty_months))
            device_id = c.lastrowid

            # Связь device_retailer (много продавцов)
            for i in range(len(retailer_ids)):
                c.execute("""
                    INSERT INTO device_retailers (device_id, retailer_id, price, in_stock, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                """, (device_id, retailer_ids[i], site_prices[i], in_stocks[i], last_updateds[i]))
            conn.commit()
        flash('Устройство добавлено. При желании заполните дополнительные характеристики.', 'success')
        return redirect(url_for('add_device', open_extras_for=device_id))

    # Ограничения по датам
    min_date = '1990-01-01'
    
    today = datetime.date.today().isoformat()
    max_date = today

    return render_template('add_device.html',
        manufacturers=manufacturers,
        categories=categories,
        operating_systems=operating_systems,
        retailers=retailers,
        colors=colors,
        min_date=min_date,
        max_date=max_date,
        today=today,
        message=message)

@app.route('/all_devices')
def all_devices():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT d.device_id, d.model, c.name as category, m.name as manufacturer
            FROM devices d
            JOIN categories c ON d.category_id = c.category_id
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            ORDER BY d.device_id
        """)
        devices = c.fetchall()
    return render_template("all_devices.html", devices=devices)

@app.route('/device/<int:device_id>')
def device_detail(device_id):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Основная информация
        c.execute("""
            SELECT d.model, c.name as category, m.name as manufacturer, d.release_date, d.current_price, d.is_waterproof, d.warranty_months,
                   col.name as color
            FROM devices d
            JOIN categories c ON d.category_id = c.category_id
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            JOIN color col ON d.color_id = col.color_id
            WHERE d.device_id = ?
        """, (device_id,))
        main = c.fetchone()

        # Продавцы
        c.execute("""
            SELECT r.name, r.website, dr.price, dr.in_stock, dr.last_updated
            FROM device_retailers dr
            JOIN retailers r ON dr.retailer_id = r.retailer_id
            WHERE dr.device_id = ?
        """, (device_id,))
        retailers = c.fetchall()

        # Операционная система
        c.execute("""
            SELECT osn.name, osys.developer, osys.latest_version, osys.release_date
            FROM devices d
            JOIN operating_systems osys ON d.os_id = osys.os_id
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
            WHERE d.device_id = ?
        """, (device_id,))
        os_info = c.fetchone()

        # Дисплей
        c.execute("""
            SELECT disp.diagonal_inches, disp.resolution, tm.name as matrix_type, disp.refresh_rate_hz, disp.brightness_nits
            FROM displays disp
            JOIN techn_matr tm ON disp.techn_matr_id = tm.techn_matr_id
            WHERE disp.device_id = ?
        """, (device_id,))
        display = c.fetchone()

        # Характеристики
        c.execute("""
            SELECT pm.name as proc_model, s.processor_cores, s.ram_gb, s.storage_gb, st.name as storage_type
            FROM specifications s
            JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
            JOIN storage_type st ON s.storage_type_id = st.storage_type_id
            WHERE s.device_id = ?
        """, (device_id,))
        specs = c.fetchone()

        # Батарея
        c.execute("""
            SELECT capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours
            FROM batteries
            WHERE device_id = ?
        """, (device_id,))
        battery = c.fetchone()

        # Камера
        c.execute("""
            SELECT megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
            FROM cameras
            WHERE device_id = ?
        """, (device_id,))
        camera = c.fetchone()
    if request.args.get('added'):
        flash('Устройство добавлено', 'success')
    return render_template(
        "device_detail.html",
        device_id=device_id,
        main=main,
        retailers=retailers,
        os_info=os_info,
        display=display,
        specs=specs,
        battery=battery,
        camera=camera
    )

@app.route('/table')
def tables_list():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Получаем имена всех таблиц в базе
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in c.fetchall()]
    return render_template('table_list.html', tables=tables)
@app.route('/table/<table_name>')
def table_view(table_name):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table_name})")
        cols_info = c.fetchall()
        columns = [desc[1] for desc in cols_info]
        pk_name = cols_info[0][1] if cols_info else None

        c.execute(f"SELECT * FROM {table_name}")
        rows = c.fetchall()

    is_dictionary = table_name in {
        'categories','manufacturers','retailers','color',
        'country','os_name','proc_model','storage_type','techn_matr'
    }
    return render_template(
        'table_view.html',
        table=table_name,
        columns=columns,
        rows=rows,
        pk_name=pk_name,
        is_dictionary=is_dictionary
    )

@app.route('/add/<table_name>', methods=['GET', 'POST'])
def add_row(table_name):
    next_url = request.args.get('next') or request.form.get('next_url') or ''
    embedded = request.form.get('embedded') or request.args.get('embedded')

    # devices добавляем на отдельной странице
    if request.method == 'GET' and table_name == "devices":
        dest = url_for('add_device')
        if embedded:
            dest += ('&' if '?' in dest else '?') + 'embedded=1'
        return redirect(dest)

    # Получаем список колонок и убираем PK *_id, если он первый
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table_name})")
        cols_info = c.fetchall()
    columns = [col[1] for col in cols_info]
    if columns and columns[0].endswith('_id'):
        columns = columns[1:]

    if request.method == 'POST':
        values = [request.form.get(col) for col in columns]
        placeholders = ','.join('?' for _ in columns)
        query = f'INSERT INTO {table_name} ({",".join(columns)}) VALUES ({placeholders})'
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(query, values)
            conn.commit()

        # Возврат назад (в модалку/родительскую страницу)
        if next_url:
            if embedded and 'embedded=' not in next_url:
                next_url += ('&' if '?' in next_url else '?') + 'embedded=1'
            return redirect(next_url)

        # Фоллбек — на просмотр таблицы, тоже без навбара в модалке
        dest = url_for('table_view', table_name=table_name)
        if embedded:
            dest += ('&' if '?' in dest else '?') + 'embedded=1'
        return redirect(dest)

    # GET: отрисовать универсальную форму
    return render_template(
        'add_form.html',
        table=table_name,
        columns=columns,
        next_url=next_url,
        embedded=embedded  # на случай, если шаблон это учитывает
    )

@app.route('/delete/<table_name>/<pk>', methods=['POST'])
def delete_row(table_name, pk):
    # для устройств используем специальный каскадный роут
    if table_name == 'devices':
        return redirect(url_for('delete_device', device_id=pk))

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        pk_name = get_pk_name(c, table_name)

        # блокируем удаление «справочников» и других таблиц, если используются
        in_use, where = value_in_use(c, table_name, pk)
        if in_use:
            flash(f"Нельзя удалить: значение используется ({where}).", "danger")
            return redirect(url_for('table_view', table_name=table_name))

        c.execute(f"DELETE FROM {table_name} WHERE {pk_name} = ?", (pk,))
        conn.commit()

    flash("Удалено", "success")
    return redirect(url_for('table_view', table_name=table_name))


@app.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Важно: сначала удалить ЗАВИСИМЫЕ строки из всех таблиц!
        c.execute("DELETE FROM displays WHERE device_id=?", (device_id,))
        c.execute("DELETE FROM specifications WHERE device_id=?", (device_id,))
        c.execute("DELETE FROM cameras WHERE device_id=?", (device_id,))
        c.execute("DELETE FROM batteries WHERE device_id=?", (device_id,))
        c.execute("DELETE FROM device_retailers WHERE device_id=?", (device_id,))
        # Теперь саму строку из Devices
        c.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
        conn.commit()
    flash("Устройство и все связанные данные удалены", "success")
    return redirect(url_for('table_view', table_name='devices'))

@app.route('/statistic', methods=['GET', 'POST'])
def statistic():
    info_by = request.form.get('info_by') or request.args.get('info_by') or 'category'

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        # ---- KPI цен (по устройствам) ----
        c.execute("""
            SELECT MIN(current_price), AVG(current_price), MAX(current_price)
            FROM devices
            WHERE current_price IS NOT NULL
        """)
        min_p, avg_p, max_p = c.fetchone()
        min_price = int(min_p) if min_p is not None else 0
        avg_price = int(avg_p) if avg_p is not None else 0
        max_price = int(max_p) if max_p is not None else 0

        # Топ-5 дешёвых / дорогих устройств
        c.execute("""
            SELECT d.device_id, d.model, d.current_price
            FROM devices d
            WHERE d.current_price IS NOT NULL
            ORDER BY d.current_price ASC
            LIMIT 5
        """)
        cheapest = c.fetchall()

        c.execute("""
            SELECT d.device_id, d.model, d.current_price
            FROM devices d
            WHERE d.current_price IS NOT NULL
            ORDER BY d.current_price DESC
            LIMIT 5
        """)
        expensive = c.fetchall()

        # ---- Контроль качества данных ----
        c.execute("""
            SELECT COUNT(*)
            FROM devices d
            LEFT JOIN specifications s ON s.device_id = d.device_id
            WHERE s.device_id IS NULL
        """)
        devices_without_specs = c.fetchone()[0] or 0

        try:
            c.execute("SELECT COUNT(*) FROM devices WHERE COALESCE(is_waterproof, 0)=0")
            devices_without_waterproof = c.fetchone()[0] or 0
        except sqlite3.Error:
            devices_without_waterproof = 0
        

        # ---- СВОДКА ПО ТАБЛИЦАМ (для statistic.html) ----
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
            'device_retailers': 'дополнительная',
            'batteries': 'дополнительная',
            'cameras': 'дополнительная',
            'displays': 'дополнительная',
            'specifications': 'дополнительная',
            'operating_systems': 'дополнительная',
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
        }

        c.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        table_names = [r[0] for r in c.fetchall()]

        tables_info = []
        for name in table_names:
            try:
                c.execute(f"SELECT COUNT(*) FROM {name}")
                rows = c.fetchone()[0] or 0
            except Exception:
                rows = 0
            c.execute(f"PRAGMA table_info({name})")
            cols = len(c.fetchall())
            tables_info.append({
                'name': name,
                'rus': rus_desc.get(name, '—'),
                'purpose': purpose_map.get(name, 'дополнительная'),
                'rows': rows,
                'columns': cols
            })

        order = {'главная': 0, 'справочник': 1, 'дополнительная': 2}
        tables_info.sort(key=lambda x: (order.get(x['purpose'], 99), x['name']))


        # ---- ГРУППИРОВКИ (без нулевых групп) ----
        # Формат: (name, devices_count, in_stock_devices, min_price, avg_price, max_price)

        # Категории: считаем по устройствам; "в наличии" — если есть хотя бы один оффер in_stock=1
        c.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(in_stock) AS in_stock_any
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
            for r in c.fetchall()
        ]

        # Производители
        c.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(in_stock) AS in_stock_any
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
            for r in c.fetchall()
        ]

        # Страны — через производителей, без нулевых
        c.execute("""
            WITH dr_any AS (
              SELECT device_id, MAX(in_stock) AS in_stock_any
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
            for r in c.fetchall()
        ]

        # Продавцы — уникальные устройства у продавца; "в наличии" у этого продавца; только не нулевые
        c.execute("""
            SELECT r.name,
                   COUNT(DISTINCT dr.device_id)                                  AS devices_count,
                   COUNT(DISTINCT CASE WHEN dr.in_stock=1 THEN dr.device_id END) AS in_stock_devices,
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
            for r in c.fetchall()
        ]

        # ---- Какая группировка активна сейчас
        if info_by == 'manufacturer':
            info_results = manufacturers_stat
        elif info_by == 'retailer':
            info_results = retailers_stat
        elif info_by == 'country':
            info_results = countries_stat
        else:
            info_by = 'category'
            info_results = categories_stat

        # ---- Данные для графика ценовой динамики (4 категории, цены отсортированы по убыванию) ----
        c.execute("""
            SELECT c.category_id, c.name, COUNT(d.device_id) AS cnt
            FROM categories c
            JOIN devices d ON d.category_id = c.category_id AND d.current_price IS NOT NULL
            GROUP BY c.category_id, c.name
            ORDER BY cnt DESC, c.name
            LIMIT 4
        """)
        top4 = c.fetchall()

        price_lines = []   # [{ 'name': str, 'prices': [int, ...] }, ...]
        max_series_len = 0
        max_series_price = 0

        for cat_id, cat_name, _ in top4:
            c.execute("""
                SELECT current_price
                FROM devices
                WHERE category_id = ? AND current_price IS NOT NULL
                ORDER BY current_price DESC
            """, (cat_id,))
            prices = [int(row[0]) for row in c.fetchall()]
            # ограничим точки для читаемости, например 60
            prices = prices[:60]
            if prices:
                price_lines.append({'name': cat_name, 'prices': prices})
                if len(prices) > max_series_len:
                    max_series_len = len(prices)
                if max(prices) > max_series_price:
                    max_series_price = max(prices)
    
    c.execute("""
        SELECT c.category_id, c.name, COUNT(d.device_id) AS cnt
        FROM categories c
        JOIN devices d ON d.category_id = c.category_id AND d.current_price IS NOT NULL
        GROUP BY c.category_id, c.name
        ORDER BY cnt DESC, c.name
        LIMIT 4
    """)
    top4 = c.fetchall()

    price_lines = []
    max_series_len = 0
    max_series_price = 0

    for cat_id, cat_name, _ in top4:
        c.execute("""
            SELECT current_price
            FROM devices
            WHERE category_id = ? AND current_price IS NOT NULL
            ORDER BY current_price ASC   -- было DESC, теперь ASC
        """, (cat_id,))
        prices = [int(row[0]) for row in c.fetchall()]
        prices = prices[:60]  # ограничим точки для читаемости
        if prices:
            price_lines.append({'name': cat_name, 'prices': prices})
            if len(prices) > max_series_len:
                max_series_len = len(prices)
            mp = max(prices)
            if mp > max_series_price:
                max_series_price = mp
    return render_template(
        'statistic.html',
        info_by=info_by,
        info_results=info_results,
        min_price=min_price, avg_price=avg_price, max_price=max_price,
        cheapest=cheapest, expensive=expensive,
        devices_without_specs=devices_without_specs,
        devices_without_waterproof=devices_without_waterproof,
        # данные для мини-графика категорий (можно оставить, если используете)
        top_cat=[(r[0], r[1]) for r in categories_stat[:5]],
        # данные для нового многосерийного графика
        price_lines=price_lines,
        price_lines_max_x=max_series_len if max_series_len else 1,
        price_lines_max_y=max_series_price if max_series_price else 1,
        tables_info=tables_info
    )

@app.route('/search', methods=['GET', 'POST'])
def search():
    mode = request.form.get('mode', 'by1')  # by1: производитель; by2: страна+цвет
    results = None

    # выбранные значения (для сохранения состояния формы)
    selected_manufacturer = request.form.get('manufacturer_id') if request.method == 'POST' else ''
    selected_country      = request.form.get('country_id')      if request.method == 'POST' else ''
    selected_color        = request.form.get('color_id')        if request.method == 'POST' else ''

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        # 1) Только те производители/страны, по которым реально есть устройства
        c.execute("""
            SELECT DISTINCT m.manufacturer_id, m.name
            FROM manufacturers m
            JOIN devices d ON d.manufacturer_id = m.manufacturer_id
            ORDER BY m.name
        """)
        manufacturers = c.fetchall()

        c.execute("""
            SELECT DISTINCT co.country_id, co.name
            FROM country co
            JOIN manufacturers m ON m.country_id = co.country_id
            JOIN devices d ON d.manufacturer_id = m.manufacturer_id
            ORDER BY co.name
        """)
        countries = c.fetchall()

        # Общий SELECT, совпадающий с колонками вашей таблицы результатов
        base_select = '''
            SELECT
                d.device_id,
                d.model,
                m.name  AS manufacturer,
                c.name  AS category,
                col.name AS color,
                co.name AS country,
                st.name AS storage_type,
                pm.name AS proc_model,
                tm.name AS techn_matr,
                d.current_price
            FROM devices d
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

        if request.method == 'POST':
            if mode == 'by1' and selected_manufacturer:
                q = base_select + ' AND d.manufacturer_id = ? GROUP BY d.device_id ORDER BY d.model'
                c.execute(q, (selected_manufacturer,))
                results = c.fetchall()
            elif mode == 'by2' and selected_country and selected_color:
                q = base_select + ' AND m.country_id = ? AND d.color_id = ? GROUP BY d.device_id ORDER BY d.model'
                c.execute(q, (selected_country, selected_color))
                results = c.fetchall()

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
    # теперь больше вариантов!
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
    # основной join для поиска только реально существующих значений!
    join_map = {
        'category':      'devices',
        'manufacturer':  'devices',
        'color':         'devices',
        'storage_type':  'specifications',
        'country':       'manufacturers',
        'retailer':      'device_retailers',
    }
    device_join = join_map[attr]
    query = f"SELECT DISTINCT t.{id_field}, t.{name_field} FROM {table} t "
    if attr == 'storage_type':
        query += "JOIN specifications s ON t.storage_type_id = s.storage_type_id JOIN devices d ON s.device_id = d.device_id "
    elif attr == 'country':
        query += "JOIN manufacturers m ON t.country_id = m.country_id JOIN devices d ON m.manufacturer_id = d.manufacturer_id "
    elif attr == 'retailer':
        query += "LEFT JOIN device_retailers dr ON t.retailer_id = dr.retailer_id JOIN devices d ON dr.device_id = d.device_id "
    else:
        query += f"JOIN devices d ON d.{id_field} = t.{id_field} "
    params = []
    # фильтрация по другому атрибуту, если надо
    if other_attr in sql_map and other_val:
        other_table, other_id_field, _ = sql_map[other_attr]
        if other_attr == 'storage_type':
            query += "LEFT JOIN s2 ON d.device_id = s2.device_id "
            query += "AND s2.storage_type_id = ? "
        elif other_attr == 'country':
            query += "JOIN manufacturers m2 ON d.manufacturer_id = m2.manufacturer_id "
            query += "AND m2.country_id = ? "
        elif other_attr == 'retailer':
            query += "LEFT JOIN device_retailers dr2 ON d.device_id = dr2.device_id "
            query += "AND dr2.retailer_id = ? "
        else:
            query += f"AND d.{other_id_field} = ? "
        params.append(other_val)
    query += f"ORDER BY t.{name_field}"
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(query, params)
        data = [{'id': row[0], 'name': row[1]} for row in c.fetchall()]
    return jsonify(data)

@app.route('/api/category_price_range')
def api_category_price_range():
    category_id = request.args.get('category_id')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if category_id and category_id != "all":
            c.execute("SELECT MIN(current_price), MAX(current_price) FROM devices WHERE category_id = ?", (category_id,))
        else:
            c.execute("SELECT MIN(current_price), MAX(current_price) FROM devices")
        row = c.fetchone()
    min_price = int(row[0] or 0)
    max_price = int(row[1] or 0)
    return {'min': min_price, 'max': max_price}

@app.route('/api/auto_search')
def api_auto_search():
    category_id = request.args.get('category_id', "all")
    manufacturer_id = request.args.get('manufacturer_id', "all")
    color_id = request.args.get('color_id', "all")

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        query = '''
            SELECT
                d.device_id,
                d.model,
                m.name  AS manufacturer,
                c.name  AS category,
                col.name AS color,
                co.name AS country,
                st.name AS storage_type,
                pm.name AS proc_model,
                tm.name AS techn_matr,
                d.current_price
            FROM devices d
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
        params = []
        if category_id != "all":
            query += " AND d.category_id = ?"
            params.append(category_id)
        if manufacturer_id != "all":
            query += " AND d.manufacturer_id = ?"
            params.append(manufacturer_id)
        if color_id != "all":
            query += " AND d.color_id = ?"
            params.append(color_id)
        c.execute(query, params)
        results = c.fetchall()
    return render_template('search_results_table.html', results=results)

@app.route('/api/filter_options')
def api_filter_options():
    category_id = request.args.get('category_id')
    manufacturer_id = request.args.get('manufacturer_id')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Производители
        q = "SELECT DISTINCT m.manufacturer_id, m.name FROM manufacturers m JOIN devices d ON m.manufacturer_id = d.manufacturer_id WHERE 1=1"
        params = []
        if category_id and category_id != "all":
            q += " AND d.category_id = ?"
            params.append(category_id)
        c.execute(q, params)
        manufacturers = [{'manufacturer_id': row[0], 'name': row[1]} for row in c.fetchall()]
        # Цвета
        q = """
            SELECT DISTINCT col.color_id, col.name
            FROM color col
            JOIN devices d ON col.color_id = d.color_id
            WHERE 1=1
        """
        params = []
        if category_id and category_id != "all":
            q += " AND d.category_id = ?"
            params.append(category_id)
        if manufacturer_id and manufacturer_id != "all":
            q += " AND d.manufacturer_id = ?"
            params.append(manufacturer_id)
        c.execute(q, params)
        colors = [{'color_id': row[0], 'name': row[1]} for row in c.fetchall()]
    return jsonify({'manufacturers': manufacturers, 'colors': colors})


@app.route('/add_manufacturer', methods=['GET', 'POST'])
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
        
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO manufacturers (name, country_id, foundation_year, website) VALUES (?, ?, ?, ?)",
                      (name, country_id, foundation_year, website))
            conn.commit()
        next_url = request.form.get('next_url')
        flash('Производитель добавлен!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_manufacturer'))
    # Получаем страны
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # страны для выпадающего списка
        c.execute("SELECT country_id, name FROM country ORDER BY name COLLATE NOCASE")
        countries = c.fetchall()
    return render_template('add_manufacturer.html', countries=countries, message=message, next_url=next_url)

@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    next_url = request.args.get('next')
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
            conn.commit()
        next_url = request.form.get('next_url')
        flash('Категория добавлена!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_category'))
    return render_template('add_category.html', next_url=next_url)

@app.route('/add_os', methods=['GET', 'POST'])
def add_os():
    today = datetime.date.today().isoformat()
    next_url = request.args.get('next')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT os_name_id, name FROM os_name ORDER BY name COLLATE NOCASE")
        os_names = c.fetchall()
    if request.method == 'POST':
        os_name_id = request.form.get('os_name_id')
        developer = request.form.get('developer')
        latest_version = request.form.get('latest_version')
        release_date = request.form.get('release_date')
        # TODO: валидация данных
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO operating_systems (os_name_id, developer, latest_version, release_date)
                VALUES (?, ?, ?, ?)
            """, (os_name_id, developer, latest_version, release_date))
            conn.commit()
        flash('Операционная система добавлена!', 'success')
        if request.form.get('next_url'):
            return redirect(request.form['next_url'])
        return redirect(url_for('add_os'))
    return render_template('add_os.html', os_names=os_names, today=today, next_url=next_url)

@app.route('/add_retailer', methods=['GET', 'POST'])
def add_retailer():
    next_url = request.args.get('next')
    if request.method == 'POST':
        name = request.form.get('name')
        website = request.form.get('website')
        rating = request.form.get('rating')
        # Валидация рейтинга
        try:
            rating_val = float(rating)
            if not (0.1 <= rating_val <= 5.0):
                raise ValueError
        except:
            flash("Рейтинг должен быть от 0.1 до 5.0", "danger")
            return redirect(request.url)
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO retailers (name, website, rating) VALUES (?, ?, ?)", (name, website, rating))
            conn.commit()
        next_url = request.form.get('next_url')
        flash('Продавец добавлен!', 'success')
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('add_retailer'))
    return render_template('add_retailer.html', next_url=next_url)

@app.route('/device/<int:device_id>/extras', methods=['GET', 'POST'])
def edit_extras(device_id):
    embedded_flag = request.args.get('embedded') or request.form.get('embedded')

    def back_to_extras():
        if embedded_flag:
            return redirect(url_for('edit_extras', device_id=device_id, embedded=1))
        return redirect(url_for('edit_extras', device_id=device_id))

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Для выпадающих списков
        c.execute("SELECT proc_model_id, name FROM proc_model ORDER BY name COLLATE NOCASE")
        proc_models = c.fetchall()
        c.execute("SELECT storage_type_id, name FROM storage_type ORDER BY name COLLATE NOCASE")
        storage_types = c.fetchall()
        c.execute("SELECT techn_matr_id, name FROM techn_matr ORDER BY name COLLATE NOCASE")
        techn_matrices = c.fetchall()
        # Текущие значения
        c.execute("SELECT * FROM specifications WHERE device_id=?", (device_id,))
        spec = c.fetchone()
        c.execute("SELECT * FROM displays WHERE device_id=?", (device_id,))
        display = c.fetchone()
        c.execute("SELECT * FROM cameras WHERE device_id=?", (device_id,))
        camera = c.fetchone()
        c.execute("SELECT * FROM batteries WHERE device_id=?", (device_id,))
        battery = c.fetchone()

    if request.method == 'POST':
        tab = request.form.get('tab')
        # SPECIFICATION
        if tab == 'specification':
            data = {}
            proc_model_id = request.form.get('proc_model_id')
            if proc_model_id: data['proc_model_id'] = proc_model_id
            processor_cores = request.form.get('processor_cores')
            if processor_cores:
                try:
                    processor_cores = int(processor_cores)
                    if not (1 <= processor_cores <= 20): raise ValueError
                    data['processor_cores'] = processor_cores
                except:
                    flash('Ядер: 1-20', 'danger')
                    return back_to_extras()
            ram_gb = request.form.get('ram_gb')
            if ram_gb:
                try:
                    ram_gb = int(ram_gb)
                    if not (1 <= ram_gb <= 32): raise ValueError
                    data['ram_gb'] = ram_gb
                except:
                    flash('RAM: 1-32', 'danger')
                    return back_to_extras()
            storage_gb = request.form.get('storage_gb')
            if storage_gb:
                try:
                    storage_gb = int(storage_gb)
                    if not (1 <= storage_gb <= 2048): raise ValueError
                    data['storage_gb'] = storage_gb
                except:
                    flash('Память: 1-2048', 'danger')
                    return back_to_extras()
            storage_type_id = request.form.get('storage_type_id')
            if storage_type_id: data['storage_type_id'] = storage_type_id

            if data:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT spec_id FROM specifications WHERE device_id=?", (device_id,))
                    exists = c.fetchone()
                    if exists:
                        # UPDATE только указанные поля
                        fields = ", ".join([f"{k}=?" for k in data])
                        values = list(data.values()) + [device_id]
                        c.execute(f"UPDATE specifications SET {fields} WHERE device_id=?", values)
                    else:
                        # INSERT только указанные
                        fields = ", ".join(['device_id'] + list(data.keys()))
                        vals = ", ".join(['?'] * (len(data) + 1))
                        values = [device_id] + list(data.values())
                        c.execute(f"INSERT INTO specifications ({fields}) VALUES ({vals})", values)
                    conn.commit()
                flash("Спецификация обновлена", "success")
        # DISPLAY
        elif tab == 'display':
            data = {}
            diagonal_inches = request.form.get('diagonal_inches')
            brightness_nits = request.form.get('brightness_nits')
            resolution = request.form.get('resolution')
            techn_matr_id = request.form.get('techn_matr_id')
            refresh_rate_hz = request.form.get('refresh_rate_hz')

            if not (diagonal_inches and resolution and techn_matr_id and refresh_rate_hz and brightness_nits):
                flash('Заполните все поля для вкладки "Дисплей".', 'danger')
                return back_to_extras()

            if diagonal_inches:
                try:
                    diagonal_inches = float(diagonal_inches)
                    if not (1.0 <= diagonal_inches <= 100.0): raise ValueError
                    data['diagonal_inches'] = diagonal_inches
                except:
                    flash('Диагональ: 1.0-100.0', 'danger')
                    return back_to_extras()
            
            if resolution:
                if not (7 <= len(resolution) <= 9):
                    flash('Разрешение: 7-9 символов', 'danger')
                    return back_to_extras()
                data['resolution'] = resolution
            
            if techn_matr_id: data['techn_matr_id'] = techn_matr_id
            
            if refresh_rate_hz:
                try:
                    refresh_rate_hz = int(refresh_rate_hz)
                    if not (1 <= refresh_rate_hz <= 360): raise ValueError
                    data['refresh_rate_hz'] = refresh_rate_hz
                except:
                    flash('Частота: 1-360', 'danger')
                    return back_to_extras()

            if brightness_nits:
                try:
                    brightness_nits = int(brightness_nits)
                    if not (1 <= brightness_nits <= 10000): raise ValueError
                    data['brightness_nits'] = brightness_nits
                except:
                    flash('Яркость: 1-10000', 'danger')
                    return back_to_extras()

            if data:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT display_id FROM displays WHERE device_id=?", (device_id,))
                    exists = c.fetchone()
                    if exists:
                        fields = ", ".join([f"{k}=?" for k in data])
                        values = list(data.values()) + [device_id]
                        c.execute(f"UPDATE displays SET {fields} WHERE device_id=?", values)
                    else:
                        fields = ", ".join(['device_id'] + list(data.keys()))
                        vals = ", ".join(['?'] * (len(data) + 1))
                        values = [device_id] + list(data.values())
                        c.execute(f"INSERT INTO displays ({fields}) VALUES ({vals})", values)
                    conn.commit()
                flash("Дисплей обновлён", "success")
        # CAMERA
        elif tab == 'camera':
            data = {}
            megapixels_main = request.form.get('megapixels_main')
            aperture_main = request.form.get('aperture_main')
            optical_zoom_x = request.form.get('optical_zoom_x')
            video_resolution = request.form.get('video_resolution')
            has_ai_enhance = 1 if request.form.get('has_ai_enhance') else 0
            data['has_ai_enhance'] = has_ai_enhance

            if not (megapixels_main and aperture_main and optical_zoom_x and video_resolution):
                flash('Заполните все поля для вкладки "Камера".', 'danger')
                return back_to_extras()
            
            if megapixels_main:
                try:
                    megapixels_main = float(megapixels_main)
                    if not (2.0 <= megapixels_main <= 33.0): raise ValueError
                    data['megapixels_main'] = megapixels_main
                except:
                    flash('Мпикс: 2.0-33.0', 'danger')
                    return back_to_extras()

            if aperture_main:
                if not (3 <= len(aperture_main) <= 4):
                    flash('Диафрагма: 3-4 символа', 'danger')
                    return back_to_extras()
                data['aperture_main'] = aperture_main

            if optical_zoom_x:
                try:
                    optical_zoom_x = float(optical_zoom_x)
                    if not (0.0 <= optical_zoom_x <= 144.0): raise ValueError
                    data['optical_zoom_x'] = optical_zoom_x
                except:
                    flash('Зум: 0.0-144.0', 'danger')
                    return back_to_extras()

            if video_resolution:
                if not (7 <= len(video_resolution) <= 9):
                    flash('Видео: 7-9 символов', 'danger')
                    return back_to_extras()
                data['video_resolution'] = video_resolution

            if data:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT camera_id FROM cameras WHERE device_id=?", (device_id,))
                    exists = c.fetchone()
                    if exists:
                        fields = ", ".join([f"{k}=?" for k in data])
                        values = list(data.values()) + [device_id]
                        c.execute(f"UPDATE cameras SET {fields} WHERE device_id=?", values)
                    else:
                        fields = ", ".join(['device_id'] + list(data.keys()))
                        vals = ", ".join(['?'] * (len(data) + 1))
                        values = [device_id] + list(data.values())
                        c.execute(f"INSERT INTO cameras ({fields}) VALUES ({vals})", values)
                    conn.commit()
                flash("Камера обновлена", "success")
        # BATTERY
        elif tab == 'battery':
            data = {}
            capacity_mah = request.form.get('capacity_mah')
            fast_charging_w = request.form.get('fast_charging_w')
            wireless_charging = 1 if request.form.get('wireless_charging') else 0
            data['wireless_charging'] = wireless_charging
            estimated_life_hours = request.form.get('estimated_life_hours')

            if not (capacity_mah and fast_charging_w and estimated_life_hours):
                flash('Заполните все поля для вкладки "Батарея".', 'danger')
                return back_to_extras()

            if capacity_mah:
                try:
                    capacity_mah = int(capacity_mah)
                    if not (1 <= capacity_mah <= 20000): raise ValueError
                    data['capacity_mah'] = capacity_mah
                except:
                    flash('Ёмкость: 1-20000', 'danger')
                    return back_to_extras()
            
            if fast_charging_w:
                try:
                    fast_charging_w = float(fast_charging_w)
                    if not (0.0 <= fast_charging_w <= 20.0): raise ValueError
                    data['fast_charging_w'] = fast_charging_w
                except:
                    flash('Быстрая зарядка: 0.0-20.0', 'danger')
                    return back_to_extras()
            
            if estimated_life_hours:
                try:
                    estimated_life_hours = float(estimated_life_hours)
                    if not (0.0 <= estimated_life_hours <= 96.0): raise ValueError
                    data['estimated_life_hours'] = estimated_life_hours
                except:
                    flash('Время работы: 0.0-96.0', 'danger')
                    return back_to_extras()

            if data:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT battery_id FROM batteries WHERE device_id=?", (device_id,))
                    exists = c.fetchone()
                    if exists:
                        fields = ", ".join([f"{k}=?" for k in data])
                        values = list(data.values()) + [device_id]
                        c.execute(f"UPDATE batteries SET {fields} WHERE device_id=?", values)
                    else:
                        fields = ", ".join(['device_id'] + list(data.keys()))
                        vals = ", ".join(['?'] * (len(data) + 1))
                        values = [device_id] + list(data.values())
                        c.execute(f"INSERT INTO batteries ({fields}) VALUES ({vals})", values)
                    conn.commit()
                flash("Батарея обновлена", "success")
        return back_to_extras()

    return render_template('edit_extras.html',
        device_id=device_id,
        proc_models=proc_models,
        storage_types=storage_types,
        techn_matrices=techn_matrices,
        spec=spec,
        display=display,
        camera=camera,
        battery=battery
    )

if __name__ == '__main__':
    app.run(debug=True)
    #app.run(host="0.0.0.0", port=5000, debug=True)
