from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import sqlite3
import os
import datetime
import re
import datetime

app = Flask(__name__)
app.secret_key = 'your-unique-secret-key-1234567890'
DB_PATH = os.path.join(os.path.dirname(__file__), 'db', '2lr.db')


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


@app.route('/')
def index():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        stats = {}
        # Подсчёт основных сущностей
        for table in ['devices', 'categories', 'manufacturers', 'retailers']:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = c.fetchone()[0]
        # Средняя цена
        c.execute("SELECT AVG(current_price) FROM devices")
        row = c.fetchone()
        avg_price = int(row[0]) if row and row[0] else 0
        # Последнее обновление (берём самую свежую дату обновления устройства или продавца)
        c.execute("SELECT MAX(last_updated) FROM device_retailers")
        last_update = c.fetchone()[0] or "нет данных"
    return render_template('index.html',
                           stats=stats,
                           avg_price=avg_price,
                           last_update=last_update)


@app.route('/add_device', methods=['GET', 'POST'])
def add_device():
    message = ""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Получаем значения для списков
        c.execute("SELECT manufacturer_id, name FROM manufacturers")
        manufacturers = c.fetchall()
        c.execute("SELECT category_id, name FROM categories")
        categories = c.fetchall()
        c.execute("""
            SELECT osys.os_id, osn.name, osys.latest_version
            FROM operating_systems osys
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
            ORDER BY osn.name, osys.latest_version
        """)
        operating_systems = c.fetchall()
        c.execute("SELECT retailer_id, name FROM retailers")
        retailers = c.fetchall()
        c.execute("SELECT color_id, name FROM color")
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
            if not ("2016-01-01" <= last_updated <= "2025-05-31"):
                flash('Дата обновления у продавца должна быть c 01.01.2016 по 31.05.2025', 'danger')
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
            if not ("1990-01-01" <= release_date <= "2025-05-31"):
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
        flash('Устройство успешно добавлено!', 'success')
        return redirect(url_for('device_detail', device_id=device_id))

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


@app.route('/report')
def report():
    # Пример простого отчёта: топ-5 самых дешёвых устройств в продаже
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT d.model, r.name AS retailer, dr.price
            FROM device_retailers dr
            JOIN devices d ON dr.device_id = d.device_id
            JOIN retailers r ON dr.retailer_id = r.retailer_id
            WHERE dr.in_stock = 1
            ORDER BY dr.price ASC
            LIMIT 5;
        """)
        rows = c.fetchall()
    return render_template('report.html', rows=rows)

@app.route('/queries', methods=['GET', 'POST'])
def queries():
    result = None
    columns = []
    query_name = request.form.get('query_name') if request.method == 'POST' else None

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if query_name == 'all_manufacturers_countries':
            c.execute("""
                SELECT m.name as manufacturer, co.name as country
                FROM manufacturers m
                JOIN country co ON m.country_id = co.country_id
            """)
            columns = ["Производитель", "Страна"]
            result = c.fetchall()
        elif query_name == 'waterproof_devices':
            c.execute("""
                SELECT d.model, m.name as manufacturer, c.name as category
                FROM devices d
                JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                JOIN categories c ON d.category_id = c.category_id
                WHERE d.is_waterproof = 1
            """)
            columns = ["Модель", "Производитель", "Категория"]
            result = c.fetchall()
        elif query_name == 'cheapest_5':
            c.execute("""
                SELECT d.model, r.name as retailer, dr.price
                FROM device_retailers dr
                JOIN devices d ON dr.device_id = d.device_id
                JOIN retailers r ON dr.retailer_id = r.retailer_id
                WHERE dr.in_stock = 1
                ORDER BY dr.price ASC
                LIMIT 5
            """)
            columns = ["Модель", "Магазин", "Цена"]
            result = c.fetchall()
        elif query_name == 'avg_price':
            c.execute("""
                SELECT AVG(current_price) FROM devices
            """)
            columns = ["Средняя цена"]
            row = c.fetchone()
            if row and row[0] is not None:
                avg = round(row[0])
                result = [[f"{avg} руб."]]
            else:
                result = [["нет данных"]]
    return render_template('queries.html', result=result, columns=columns)

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
        # Получаем имена столбцов таблицы
        c.execute(f"PRAGMA table_info({table_name})")
        columns = [desc[1] for desc in c.fetchall()]
        # Получаем все записи из таблицы
        c.execute(f"SELECT * FROM {table_name}")
        rows = c.fetchall()
    return render_template('table_view.html', table=table_name, columns=columns, rows=rows)

@app.route('/add/<table_name>', methods=['GET', 'POST'])
def add_row(table_name):
    next_url = request.args.get('next') or request.form.get('next_url')
    # Универсальный роут, можно кастомизировать для devices
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table_name})")
        columns = [desc[1] for desc in c.fetchall()]
        # Автоматически исключаем id, если он AUTOINCREMENT
        if columns[0].endswith("_id"):
            columns = columns[1:]
    if request.method == 'POST':
        values = [request.form.get(col) for col in columns]
        placeholders = ','.join(['?'] * len(columns))
        query = f'INSERT INTO {table_name} ({",".join(columns)}) VALUES ({placeholders})'
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(query, values)
        if next_url:
            return redirect(next_url)
        else:
            return redirect(url_for('table_view', table_name=table_name))
    # Для devices используем отдельную страницу
    if table_name == "devices":
        return redirect(url_for('add_device'))
    return render_template('add_form.html', table=table_name, columns=columns, next_url=next_url)

@app.route('/delete/<table_name>/<pk>', methods=['POST'])
def delete_row(table_name, pk):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # получаем название pk-столбца (первый столбец всегда PRIMARY KEY)
        c.execute(f"PRAGMA table_info({table_name})")
        pk_name = c.fetchone()[1]
        c.execute(f"DELETE FROM {table_name} WHERE {pk_name} = ?", (pk,))
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
    return redirect(url_for('all_devices'))


@app.route('/statistic')
def statistic():
    table_rus_names = {
        'country': 'Страны',
        'proc_model': 'Модели процессоров',
        'color': 'Цвета',
        'techn_matr': 'Технология матрицы',
        'storage_type': 'Тип накопителя',
        'os_name': 'Названия ОС',
        'categories': 'Категории устройств',
        'manufacturers': 'Производители',
        'operating_systems': 'Операционные системы',
        'devices': 'Устройства',
        'specifications': 'Характеристики памяти',
        'displays': 'Характеристики экрана',
        'cameras': 'Характеристики камеры',
        'batteries': 'Характеристики батареи',
        'retailers': 'Магазины-продавцы',
        'device_retailers': 'Продажи устройств'
    }

    stats = {}
    columns_count = {}
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Получаем список всех таблиц (кроме системных)
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in c.fetchall()]
        
        # Для каждой таблицы считаем количество записей и атрибутов
        for table in tables:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = c.fetchone()[0]
            c.execute(f"PRAGMA table_info({table})")
            columns_count[table] = len(c.fetchall())

        # Средняя, минимальная, максимальная цена устройства
        c.execute("SELECT AVG(current_price), MIN(current_price), MAX(current_price) FROM devices")
        avg_price, min_price, max_price = c.fetchone()
        avg_price = round(avg_price, 2) if avg_price else 0
        min_price = round(min_price, 2) if min_price else 0
        max_price = round(max_price, 2) if max_price else 0

        # 5 самых дешевых устройств
        c.execute("""
            SELECT device_id, model, current_price
            FROM devices
            ORDER BY current_price ASC LIMIT 5
        """)
        cheapest = c.fetchall()

        # 5 самых дорогих устройств
        c.execute("""
            SELECT device_id, model, current_price
            FROM devices
            ORDER BY current_price DESC LIMIT 5
        """)
        expensive = c.fetchall()

        # Количество устройств без технических характеристик
        c.execute("""
            SELECT COUNT(*) FROM devices d
            LEFT JOIN specifications s ON d.device_id = s.device_id
            WHERE s.device_id IS NULL
        """)
        devices_without_specs = c.fetchone()[0]

        # Количество устройств без водонепроницаемости
        c.execute("SELECT COUNT(*) FROM devices WHERE is_waterproof = 0")
        devices_without_waterproof = c.fetchone()[0]

        # === Статистика по категориям ===
        c.execute("""
            SELECT c.name, COUNT(d.device_id)
            FROM categories c
            LEFT JOIN devices d ON c.category_id = d.category_id
            GROUP BY c.category_id, c.name
            ORDER BY COUNT(d.device_id) DESC
        """)
        categories_stat = c.fetchall()

        # === Статистика по производителям ===
        c.execute("""
            SELECT m.name, COUNT(d.device_id)
            FROM manufacturers m
            LEFT JOIN devices d ON m.manufacturer_id = d.manufacturer_id
            GROUP BY m.manufacturer_id, m.name
            ORDER BY COUNT(d.device_id) DESC
        """)
        manufacturers_stat = c.fetchall()

    return render_template(
        "statistic.html",
        stats=stats,
        columns_count=columns_count,
        avg_price=avg_price,
        min_price=min_price,
        max_price=max_price,
        cheapest=cheapest,
        expensive=expensive,
        devices_without_specs=devices_without_specs,
        devices_without_waterproof=devices_without_waterproof,
        table_rus_names=table_rus_names,
        categories_stat=categories_stat,
        manufacturers_stat=manufacturers_stat
    )

@app.route('/search', methods=['GET', 'POST'])
def search():
    mode = request.form.get('mode', 'search')
    info_by = request.form.get('info_by', 'retailer')
    info_results = None
    results = None
    filter_results = None
    results1 = None
    results2 = None
    error_message = None
    
    selected_attr1 = request.form.get('attribute1')
    selected_value1 = request.form.get('value1')
    selected_attr2a = request.form.get('attribute2a')
    selected_value2a = request.form.get('value2a')
    selected_attr2b = request.form.get('attribute2b')
    selected_value2b = request.form.get('value2b')

    selected_category = "all"
    selected_manufacturer = "all"
    selected_color = "all"
    filter_category_id = "all"
    price_min_filter = ""
    price_max_filter = ""

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT category_id, name FROM categories")
        categories = c.fetchall()
        c.execute("SELECT manufacturer_id, name FROM manufacturers")
        manufacturers = c.fetchall()
        c.execute("SELECT color_id, name FROM color")
        colors = c.fetchall()

        # Для фильтрации по цене
        c.execute("SELECT MIN(current_price), MAX(current_price) FROM devices")
        min_price_in_db, max_price_in_db = c.fetchone()
        min_price_in_db = int(min_price_in_db or 0)
        max_price_in_db = int(max_price_in_db or 1000000)

        if request.method == 'POST':
            # Стандартный поиск (3 фильтра)
            if mode == 'search':
                selected_category = request.form.get('category_id', "all")
                selected_manufacturer = request.form.get('manufacturer_id', "all")
                selected_color = request.form.get('color_id', "all")
                query = '''
                    SELECT d.device_id, d.model, m.name as manufacturer, c.name as category,
                           col.name as color,
                           s.storage_gb, st.name as storage_type, pm.name as processor_model,
                           disp.diagonal_inches,
                           d.current_price
                    FROM devices d
                    JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                    JOIN categories c ON d.category_id = c.category_id
                    JOIN color col ON d.color_id = col.color_id
                    LEFT JOIN specifications s ON d.device_id = s.device_id
                    LEFT JOIN storage_type st ON s.storage_type_id = st.storage_type_id
                    LEFT JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
                    LEFT JOIN displays disp ON d.device_id = disp.device_id
                    WHERE 1=1
                '''
                params = []
                if selected_category != "all":
                    query += " AND d.category_id = ?"
                    params.append(selected_category)
                if selected_manufacturer != "all":
                    query += " AND d.manufacturer_id = ?"
                    params.append(selected_manufacturer)
                if selected_color != "all":
                    query += " AND d.color_id = ?"
                    params.append(selected_color)
                c.execute(query, params)
                results = c.fetchall()

            # Поиск по одному атрибуту
            elif mode == 'search1':
                attr = request.form.get('attribute1')
                val = request.form.get('value1')
                query = '''
                    SELECT d.device_id, d.model, m.name as manufacturer, c.name as category,
                        col.name as color,
                        s.storage_gb, st.name as storage_type, pm.name as processor_model,
                        disp.diagonal_inches,
                        d.current_price
                    FROM devices d
                    JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                    JOIN categories c ON d.category_id = c.category_id
                    JOIN color col ON d.color_id = col.color_id
                    LEFT JOIN specifications s ON d.device_id = s.device_id
                    LEFT JOIN storage_type st ON s.storage_type_id = st.storage_type_id
                    LEFT JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
                    LEFT JOIN displays disp ON d.device_id = disp.device_id
                    LEFT JOIN device_retailers dr ON d.device_id = dr.device_id
                    WHERE 1=1
                '''
                params = []
                if attr == "category":
                    query += " AND d.category_id = ?"
                elif attr == "manufacturer":
                    query += " AND d.manufacturer_id = ?"
                elif attr == "color":
                    query += " AND d.color_id = ?"
                elif attr == "storage_type":
                    query += " AND s.storage_type_id = ?"
                elif attr == "country":
                    query += " AND m.country_id = ?"
                elif attr == "retailer":
                    query += " AND dr.retailer_id = ?"
                params.append(val)
                c.execute(query, params)
                results1 = c.fetchall()

            # Поиск по двум атрибутам
            elif mode == 'search2':
                attr_a = request.form.get('attribute2a')
                val_a = request.form.get('value2a')
                attr_b = request.form.get('attribute2b')
                val_b = request.form.get('value2b')
                query = '''
                    SELECT d.device_id, d.model, m.name as manufacturer, c.name as category,
                        col.name as color,
                        s.storage_gb, st.name as storage_type, pm.name as processor_model,
                        disp.diagonal_inches,
                        d.current_price
                    FROM devices d
                    JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                    JOIN categories c ON d.category_id = c.category_id
                    JOIN color col ON d.color_id = col.color_id
                    LEFT JOIN specifications s ON d.device_id = s.device_id
                    LEFT JOIN storage_type st ON s.storage_type_id = st.storage_type_id
                    LEFT JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
                    LEFT JOIN displays disp ON d.device_id = disp.device_id
                    LEFT JOIN device_retailers dr ON d.device_id = dr.device_id
                    WHERE 1=1
                '''
                params = []
                if attr_a == "category":
                    query += " AND d.category_id = ?"
                elif attr_a == "manufacturer":
                    query += " AND d.manufacturer_id = ?"
                elif attr_a == "color":
                    query += " AND d.color_id = ?"
                elif attr_a == "storage_type":
                    query += " AND s.storage_type_id = ?"
                elif attr_a == "country":
                    query += " AND m.country_id = ?"
                elif attr_a == "retailer":
                    query += " AND dr.retailer_id = ?"
                params.append(val_a)

                if attr_b and val_b and val_b != "all":
                    if attr_b == "category":
                        query += " AND d.category_id = ?"
                    elif attr_b == "manufacturer":
                        query += " AND d.manufacturer_id = ?"
                    elif attr_b == "color":
                        query += " AND d.color_id = ?"
                    elif attr_b == "storage_type":
                        query += " AND s.storage_type_id = ?"
                    elif attr_b == "country":
                        query += " AND m.country_id = ?"
                    elif attr_b == "retailer":
                        query += " AND dr.retailer_id = ?"
                    params.append(val_b)

                c.execute(query, params)
                results2 = c.fetchall()

            # Справка по БД
            elif mode == 'info':
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

            # Фильтрация по цене
            elif mode == 'filter':
                filter_category_id = request.form.get('filter_category_id', "all")
                price_min_filter = request.form.get('price_min', '').strip()
                price_max_filter = request.form.get('price_max', '').strip()

                price_min = None
                price_max = None
                try:
                    price_min = float(price_min_filter) if price_min_filter else None
                    price_max = float(price_max_filter) if price_max_filter else None
                    if price_min is not None and price_min < min_price_in_db:
                        error_message = f"Минимальная цена не может быть меньше {min_price_in_db}"
                    if price_max is not None and price_max > max_price_in_db:
                        error_message = f"Максимальная цена не может быть больше {max_price_in_db}"
                    if (price_min is not None and price_max is not None and price_min > price_max):
                        error_message = "Минимальная цена не может быть больше максимальной!"
                except Exception:
                    error_message = "Ошибка ввода цены!"

                actual_min = price_min if price_min is not None else min_price_in_db
                actual_max = price_max if price_max is not None else max_price_in_db

                if not error_message:
                    query = '''
                        SELECT d.device_id, d.model, m.name as manufacturer, c.name as category,
                               col.name as color,
                               s.storage_gb, st.name as storage_type, pm.name as processor_model,
                               disp.diagonal_inches,
                               d.current_price
                        FROM devices d
                        JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                        JOIN categories c ON d.category_id = c.category_id
                        JOIN color col ON d.color_id = col.color_id
                        LEFT JOIN specifications s ON d.device_id = s.device_id
                        LEFT JOIN storage_type st ON s.storage_type_id = st.storage_type_id
                        LEFT JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
                        LEFT JOIN displays disp ON d.device_id = disp.device_id
                        WHERE 1=1
                    '''
                    params = []
                    if filter_category_id != "all":
                        query += " AND d.category_id = ?"
                        params.append(filter_category_id)
                    query += " AND d.current_price >= ? AND d.current_price <= ?"
                    params.extend([actual_min, actual_max])

                    c.execute(query, params)
                    filter_results = c.fetchall()
                else:
                    filter_results = []
    if request.args.get('ajax') == '1' and mode == 'search2':
        return render_template('search_results_table.html', results=results2)
    return render_template(
        'search.html',
        categories=categories,
        manufacturers=manufacturers,
        colors=colors,
        selected_category=selected_category,
        selected_manufacturer=selected_manufacturer,
        selected_color=selected_color,
        results=results,
        mode=mode,
        info_by=info_by,
        info_results=info_results,
        filter_category_id=filter_category_id,
        price_min_filter=price_min_filter,
        price_max_filter=price_max_filter,
        filter_results=filter_results,
        min_price_in_db=min_price_in_db,
        max_price_in_db=max_price_in_db,
        results1=results1,
        results2=results2,
        selected_attr1=selected_attr1,
        selected_value1=selected_value1,
        selected_attr2a=selected_attr2a,
        selected_value2a=selected_value2a,
        selected_attr2b=selected_attr2b,
        selected_value2b=selected_value2b,
        error_message=error_message
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
            query += "JOIN LEFT JOIN s2 ON d.device_id = s2.device_id "
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
            SELECT d.device_id, d.model, m.name as manufacturer, c.name as category,
                   col.name as color,
                   s.storage_gb, st.name as storage_type, pm.name as processor_model,
                   disp.diagonal_inches,
                   d.current_price
            FROM devices d
            JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
            JOIN categories c ON d.category_id = c.category_id
            JOIN color col ON d.color_id = col.color_id
            LEFT JOIN specifications s ON d.device_id = s.device_id
            LEFT JOIN storage_type st ON s.storage_type_id = st.storage_type_id
            LEFT JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
            LEFT JOIN displays disp ON d.device_id = disp.device_id
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
        c.execute("SELECT country_id, name FROM country")
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
        c.execute("SELECT os_name_id, name FROM os_name")
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
            flash("Рейтинг должен быть от 0.9 до 5.0", "danger")
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
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Для выпадающих списков
        c.execute("SELECT proc_model_id, name FROM proc_model")
        proc_models = c.fetchall()
        c.execute("SELECT storage_type_id, name FROM storage_type")
        storage_types = c.fetchall()
        c.execute("SELECT techn_matr_id, name FROM techn_matr")
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
                    return redirect(url_for('edit_extras', device_id=device_id))
            ram_gb = request.form.get('ram_gb')
            if ram_gb:
                try:
                    ram_gb = int(ram_gb)
                    if not (1 <= ram_gb <= 32): raise ValueError
                    data['ram_gb'] = ram_gb
                except:
                    flash('RAM: 1-32', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
            storage_gb = request.form.get('storage_gb')
            if storage_gb:
                try:
                    storage_gb = int(storage_gb)
                    if not (1 <= storage_gb <= 2048): raise ValueError
                    data['storage_gb'] = storage_gb
                except:
                    flash('Память: 1-2048', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
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
                return redirect(url_for('edit_extras', device_id=device_id))

            if diagonal_inches:
                try:
                    diagonal_inches = float(diagonal_inches)
                    if not (1.0 <= diagonal_inches <= 100.0): raise ValueError
                    data['diagonal_inches'] = diagonal_inches
                except:
                    flash('Диагональ: 1.0-100.0', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
            
            if resolution:
                if not (7 <= len(resolution) <= 9):
                    flash('Разрешение: 7-9 символов', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
                data['resolution'] = resolution
            
            if techn_matr_id: data['techn_matr_id'] = techn_matr_id
            
            if refresh_rate_hz:
                try:
                    refresh_rate_hz = int(refresh_rate_hz)
                    if not (1 <= refresh_rate_hz <= 360): raise ValueError
                    data['refresh_rate_hz'] = refresh_rate_hz
                except:
                    flash('Частота: 1-360', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))

            if brightness_nits:
                try:
                    brightness_nits = int(brightness_nits)
                    if not (1 <= brightness_nits <= 10000): raise ValueError
                    data['brightness_nits'] = brightness_nits
                except:
                    flash('Яркость: 1-10000', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))

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
            has_ai_enhance = 1 if request.form.get('has_ai_enhance') == 'on' else 0

            if not (megapixels_main and aperture_main and optical_zoom_x and video_resolution):
                flash('Заполните все поля для вкладки "Камера".', 'danger')
                return redirect(url_for('edit_extras', device_id=device_id))
            
            if megapixels_main:
                try:
                    megapixels_main = float(megapixels_main)
                    if not (2.0 <= megapixels_main <= 33.0): raise ValueError
                    data['megapixels_main'] = megapixels_main
                except:
                    flash('Мпикс: 2.0-33.0', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))

            if aperture_main:
                if not (3 <= len(aperture_main) <= 4):
                    flash('Диафрагма: 3-4 символа', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
                data['aperture_main'] = aperture_main

            if optical_zoom_x:
                try:
                    optical_zoom_x = float(optical_zoom_x)
                    if not (0.0 <= optical_zoom_x <= 144.0): raise ValueError
                    data['optical_zoom_x'] = optical_zoom_x
                except:
                    flash('Зум: 0.0-144.0', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))

            if video_resolution:
                if not (7 <= len(video_resolution) <= 9):
                    flash('Видео: 7-9 символов', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
                data['video_resolution'] = video_resolution

            if has_ai_enhance is not None:
                data['has_ai_enhance'] = 1 if has_ai_enhance == 'on' else 0

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
            wireless_charging = 1 if request.form.get('wireless_charging') == 'on' else 0
            estimated_life_hours = request.form.get('estimated_life_hours')

            if not (capacity_mah and fast_charging_w and estimated_life_hours):
                flash('Заполните все поля для вкладки "Батарея".', 'danger')
                return redirect(url_for('edit_extras', device_id=device_id))

            if capacity_mah:
                try:
                    capacity_mah = int(capacity_mah)
                    if not (1 <= capacity_mah <= 20000): raise ValueError
                    data['capacity_mah'] = capacity_mah
                except:
                    flash('Ёмкость: 1-20000', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
            
            if fast_charging_w:
                try:
                    fast_charging_w = float(fast_charging_w)
                    if not (0.0 <= fast_charging_w <= 20.0): raise ValueError
                    data['fast_charging_w'] = fast_charging_w
                except:
                    flash('Быстрая зарядка: 0.0-20.0', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))
            
            if wireless_charging is not None:
                data['wireless_charging'] = 1 if wireless_charging == 'on' else 0
            
            if estimated_life_hours:
                try:
                    estimated_life_hours = float(estimated_life_hours)
                    if not (0.0 <= estimated_life_hours <= 96.0): raise ValueError
                    data['estimated_life_hours'] = estimated_life_hours
                except:
                    flash('Время работы: 0.0-96.0', 'danger')
                    return redirect(url_for('edit_extras', device_id=device_id))

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
        return redirect(url_for('edit_extras', device_id=device_id))

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
