from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
import os

app = Flask(__name__)
DB_PATH = os.path.join('db', '2lr.db')

def get_tables():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        return [row[0] for row in c.fetchall() if not row[0].startswith('sqlite_')]

@app.route('/')
def index():
    tables = get_tables()
    return render_template('index.html', tables=tables)

@app.route('/table/<table_name>')
def table_view(table_name):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table_name})")
        columns = [desc[1] for desc in c.fetchall()]
        c.execute(f"SELECT * FROM {table_name}")
        rows = c.fetchall()
    return render_template('table_view.html', table=table_name, columns=columns, rows=rows)

@app.route('/add/<table_name>', methods=['GET', 'POST'])
def add_row(table_name):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table_name})")
        columns = [desc[1] for desc in c.fetchall()]
    if request.method == 'POST':
        values = [request.form.get(col) for col in columns]
        placeholders = ','.join(['?'] * len(columns))
        query = f'INSERT INTO {table_name} ({",".join(columns)}) VALUES ({placeholders})'
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(query, values)
        return redirect(url_for('table_view', table_name=table_name))
    return render_template('add_form.html', table=table_name, columns=columns)

@app.route('/delete/<table_name>/<pk>', methods=['POST'])
def delete_row(table_name, pk):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # получаем название pk-столбца (первый столбец всегда PRIMARY KEY)
        c.execute(f"PRAGMA table_info({table_name})")
        pk_name = c.fetchone()[1]
        c.execute(f"DELETE FROM {table_name} WHERE {pk_name} = ?", (pk,))
    return redirect(url_for('table_view', table_name=table_name))
@app.route('/add/devices', methods=['GET', 'POST'])
def add_device():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT manufacturer_id, name FROM manufacturers ORDER BY name")
        manufacturers = c.fetchall()
        c.execute("SELECT category_id, name FROM categories ORDER BY name")
        categories = c.fetchall()
        c.execute("""
            SELECT os_id, osn.name FROM operating_systems osys
            JOIN os_name osn ON osys.os_name_id = osn.os_name_id
            ORDER BY osn.name
        """)
        operating_systems = c.fetchall()
        c.execute("SELECT color_id, name FROM color ORDER BY name")
        colors = c.fetchall()
        c.execute("SELECT storage_type_id, name FROM storage_type ORDER BY name")
        storage_types = c.fetchall()
        c.execute("SELECT proc_model_id, name FROM proc_model ORDER BY name")
        proc_models = c.fetchall()
        c.execute("SELECT techn_matr_id, name FROM techn_matr ORDER BY name")
        matr_types = c.fetchall()

    if request.method == 'POST':
        # Devices
        manufacturer_id = request.form.get('manufacturer_id')
        category_id = request.form.get('category_id')
        os_id = request.form.get('os_id')
        model = request.form.get('model')
        release_date = request.form.get('release_date')
        current_price = request.form.get('current_price')
        weight_grams = request.form.get('weight_grams')
        color_id = request.form.get('color_id')
        is_waterproof = request.form.get('is_waterproof')
        warranty_months = request.form.get('warranty_months')

        # Specifications
        proc_model_id = request.form.get('proc_model_id')
        processor_cores = request.form.get('processor_cores')
        ram_gb = request.form.get('ram_gb')
        storage_gb = request.form.get('storage_gb')
        storage_type_id = request.form.get('storage_type_id')

        # Display
        diagonal_inches = request.form.get('diagonal_inches')
        resolution = request.form.get('resolution')
        techn_matr_id = request.form.get('techn_matr_id')
        refresh_rate_hz = request.form.get('refresh_rate_hz')
        brightness_nits = request.form.get('brightness_nits')

        # Camera
        megapixels_main = request.form.get('megapixels_main')
        aperture_main = request.form.get('aperture_main')
        optical_zoom_x = request.form.get('optical_zoom_x')
        video_resolution = request.form.get('video_resolution')
        has_ai_enhance = request.form.get('has_ai_enhance')

        # Battery
        capacity_mah = request.form.get('capacity_mah')
        fast_charging_w = request.form.get('fast_charging_w')
        wireless_charging = request.form.get('wireless_charging')
        estimated_life_hours = request.form.get('estimated_life_hours')

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            # 1. Devices (device_id присваивается автоматически)
            c.execute("""
                INSERT INTO devices (
                    manufacturer_id, category_id, os_id, model, release_date, current_price,
                    weight_grams, color_id, is_waterproof, warranty_months
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (manufacturer_id, category_id, os_id, model, release_date, current_price,
                  weight_grams, color_id, is_waterproof, warranty_months))
            device_id = c.lastrowid

            # 2. Specifications
            c.execute("""
                INSERT INTO specifications (
                    device_id, proc_model_id, processor_cores, ram_gb, storage_gb, storage_type_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (device_id, proc_model_id, processor_cores, ram_gb, storage_gb, storage_type_id))

            # 3. Display
            c.execute("""
                INSERT INTO displays (
                    device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (device_id, diagonal_inches, resolution, techn_matr_id, refresh_rate_hz, brightness_nits))

            # 4. Camera (если поля заполнены)
            if megapixels_main and aperture_main:
                c.execute("""
                    INSERT INTO cameras (
                        device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (device_id, megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance))

            # 5. Battery
            c.execute("""
                INSERT INTO batteries (
                    device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours
                ) VALUES (?, ?, ?, ?, ?)
            """, (device_id, capacity_mah, fast_charging_w, wireless_charging, estimated_life_hours))

        return redirect(url_for('table_view', table_name='devices'))

    return render_template(
        'add_device_full.html',
        manufacturers=manufacturers,
        categories=categories,
        operating_systems=operating_systems,
        colors=colors,
        proc_models=proc_models,
        storage_types=storage_types,
        matr_types=matr_types
    )

@app.route('/edit/<table_name>/<pk>', methods=['GET', 'POST'])
def edit_row(table_name, pk):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Получить названия столбцов
        c.execute(f"PRAGMA table_info({table_name})")
        columns_info = c.fetchall()
        columns = [col[1] for col in columns_info]
        pk_name = columns[0]

        # Внешние ключи для select'ов (простая логика: *_id кроме pk)
        fk_options = {}
        for col in columns:
            if col.endswith('_id') and col != pk_name:
                ref_table = col[:-3]  # например: color_id -> color
                try:
                    c.execute(f"SELECT {col}, name FROM {ref_table} ORDER BY name")
                    fk_options[col] = c.fetchall()
                except Exception:
                    pass  # если справочника нет

        # GET: показываем текущие значения
        if request.method == 'GET':
            c.execute(f"SELECT * FROM {table_name} WHERE {pk_name} = ?", (pk,))
            row = c.fetchone()
            return render_template(
                'edit_form.html',
                table=table_name,
                columns=columns,
                row=row,
                fk_options=fk_options,
                pk=pk,
                pk_name=pk_name,
                zip=zip
            )

        # POST: обновляем
        values = []
        for idx, col in enumerate(columns):
            if col == pk_name:
                continue  # pk не редактируется
            values.append(request.form.get(col))
        # prepare SET ... part
        set_str = ', '.join([f"{col}=?" for col in columns if col != pk_name])
        values.append(pk)
        c.execute(f"UPDATE {table_name} SET {set_str} WHERE {pk_name} = ?", values)
        conn.commit()
        return redirect(url_for('table_view', table_name=table_name))


@app.route('/statistics')
def statistics():
    stats = {}
    columns_count = {}
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in c.fetchall()]
        for table in tables:
            # Количество записей
            c.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = c.fetchone()[0]
            # Количество атрибутов (столбцов)
            c.execute(f"PRAGMA table_info({table})")
            columns_count[table] = len(c.fetchall())
    return render_template('statistics.html', stats=stats, columns_count=columns_count)

@app.route('/search', methods=['GET', 'POST'])
def search():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT category_id, name FROM categories")
        categories = c.fetchall()
        c.execute("SELECT manufacturer_id, name FROM manufacturers")
        manufacturers = c.fetchall()
        c.execute("SELECT color_id, name FROM color")
        colors = c.fetchall()

    selected_category = request.form.get('category_id', "all")
    selected_manufacturer = request.form.get('manufacturer_id', "all")
    selected_color = request.form.get('color_id', "all")
    price_min = request.form.get('price_min')
    price_max = request.form.get('price_max')

    results = None
    if request.method == 'POST':
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            query = '''
                SELECT d.device_id, d.model, m.name as manufacturer, c.name as category, co.name as country,
                       d.current_price, col.name as color,
                       CASE d.is_waterproof WHEN 1 THEN 'Да' ELSE 'Нет' END as is_waterproof,
                       s.storage_gb, st.name as storage_type, pm.name as processor_model,
                       disp.diagonal_inches
                FROM devices d
                JOIN manufacturers m ON d.manufacturer_id = m.manufacturer_id
                JOIN categories c ON d.category_id = c.category_id
                JOIN country co ON m.country_id = co.country_id
                JOIN color col ON d.color_id = col.color_id
                JOIN specifications s ON d.device_id = s.device_id
                JOIN storage_type st ON s.storage_type_id = st.storage_type_id
                JOIN proc_model pm ON s.proc_model_id = pm.proc_model_id
                JOIN displays disp ON d.device_id = disp.device_id
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
            if price_min:
                query += " AND d.current_price >= ?"
                params.append(price_min)
            if price_max:
                query += " AND d.current_price <= ?"
                params.append(price_max)
            c.execute(query, params)
            results = c.fetchall()
    return render_template(
        'search.html',
        categories=categories,
        manufacturers=manufacturers,
        colors=colors,
        selected_category=selected_category,
        selected_manufacturer=selected_manufacturer,
        selected_color=selected_color,
        price_min=price_min, price_max=price_max,
        results=results
    )

@app.route('/api/filter_options')
def api_filter_options():
    category_id = request.args.get('category_id')
    manufacturer_id = request.args.get('manufacturer_id')
    color_id = request.args.get('color_id')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Динамические производители
        q = "SELECT DISTINCT m.manufacturer_id, m.name FROM manufacturers m JOIN devices d ON m.manufacturer_id = d.manufacturer_id WHERE 1=1"
        params = []
        if category_id and category_id != "all":
            q += " AND d.category_id = ?"
            params.append(category_id)
        c.execute(q, params)
        manufacturers = [{'manufacturer_id': row[0], 'name': row[1]} for row in c.fetchall()]
        
        # Динамические цвета
        q = "SELECT DISTINCT col.color_id, col.name FROM color col JOIN devices d ON col.color_id = d.color_id WHERE 1=1"
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

# --- API endpoints для JS ---

@app.route('/api/categories')
def api_categories():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT category_id, name FROM categories")
        data = [{'category_id': row[0], 'name': row[1]} for row in c.fetchall()]
    return jsonify(data)

@app.route('/api/manufacturers')
def api_manufacturers():
    category_id = request.args.get('category_id')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if category_id and category_id != "all":
            c.execute("""SELECT DISTINCT m.manufacturer_id, m.name
                        FROM manufacturers m
                        JOIN devices d ON m.manufacturer_id = d.manufacturer_id
                        WHERE d.category_id = ?""", (category_id,))
        else:
            c.execute("SELECT manufacturer_id, name FROM manufacturers")
        data = [{'manufacturer_id': row[0], 'name': row[1]} for row in c.fetchall()]
    return jsonify(data)

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

@app.route('/all_devices')
def all_devices():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Выведем основные сведения + id для ссылки
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

        # device_id — id текущего устройства
        c.execute("""
            SELECT r.name, r.website, dr.price, dr.in_stock, dr.last_updated
            FROM device_retailers dr
            JOIN retailers r ON dr.retailer_id = r.retailer_id
            WHERE dr.device_id = ?
        """, (device_id,))
        retailers = c.fetchall()

        # ОС
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

        # Камера (если есть)
        c.execute("""
            SELECT megapixels_main, aperture_main, optical_zoom_x, video_resolution, has_ai_enhance
            FROM cameras
            WHERE device_id = ?
        """, (device_id,))
        camera = c.fetchone()
        
    return render_template(
        "device_detail.html",
        main=main,
        retailers=retailers,
        os_info=os_info,
        display=display,
        specs=specs,
        battery=battery,
        camera=camera
    )

if __name__ == '__main__':
    app.run(debug=True)
