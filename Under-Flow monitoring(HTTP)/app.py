"""
Flask Backend untuk Sistem Monitoring Banjir dengan Fuzzy Logic (FloodSense+)
Author: Under-Flow — GEMASTIK XVIII 2025 Kota Cerdas

Endpoint:
  GET    /                  Dashboard
  GET    /healthz           Healthcheck (Docker)
  POST   /api/sensor-data   Terima data dari ESP32
  GET    /api/latest        Data terbaru (compat)
  GET    /api/history       Historis sederhana (compat)
  GET    /api/alerts        Alert history
  GET    /api/statistics    Statistik agregat
  POST   /api/test-fuzzy    Uji fuzzy logic tanpa insert DB
  GET    /sensor_data       Tabel dashboard (pagination, sort, search, filter tanggal)
  DELETE /sensor_data/<id>  Hapus satu record
  GET    /graph             Agregasi time-series utk Chart.js
  GET    /export_csv        Unduh CSV hasil filter
"""

from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from datetime import datetime
from io import StringIO
import csv
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Override path DB utk container (volume mount); default: file lokal di project
DB_PATH = os.environ.get("FLOOD_DB_PATH", os.path.join(BASE_DIR, "flood_monitoring.db"))
DATA_DIR = os.path.join(BASE_DIR, "data")
SEED_FILE = os.path.join(DATA_DIR, "data_latih.csv")
FALLBACK_SEED_FILE = os.path.join(DATA_DIR, "data_bersih.csv")

app = Flask(__name__)
CORS(app)


# ==================== DATABASE SETUP ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Inisialisasi database SQLite"""
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            tip_count REAL,
            rainfall REAL NOT NULL,
            water_level REAL NOT NULL,
            fuzzy_score REAL,
            status TEXT,
            rate_of_change REAL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL,
            rainfall REAL,
            water_level REAL,
            message TEXT
        )
    ''')

    # Migrasi ringan: DB lama belum punya kolom tip_count
    cols = [r[1] for r in c.execute('PRAGMA table_info(sensor_data)').fetchall()]
    if "tip_count" not in cols:
        c.execute('ALTER TABLE sensor_data ADD COLUMN tip_count REAL')

    conn.commit()
    conn.close()


def seed_from_csv():
    """
    Auto-seed: bila tabel sensor_data kosong, isi dari data_latih.csv
    (fallback: data_bersih.csv) supaya dashboard & grafik langsung terisi
    saat demo tanpa menunggu sensor fisik.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM sensor_data')
    if c.fetchone()[0] > 0:
        conn.close()
        return

    seed_path = SEED_FILE if os.path.exists(SEED_FILE) else FALLBACK_SEED_FILE
    if not os.path.exists(seed_path):
        print(f"[seed] file seed tidak ditemukan: {seed_path} — lewati seeding")
        conn.close()
        return

    print(f"[seed] mengisi sensor_data dari {os.path.basename(seed_path)} ...")
    with open(seed_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            try:
                ts = r.get("Timestamp") or r.get("timestamp")
                rain = float(r.get("Rainfall (mm)") or r.get("rainfall") or 0)
                wl = float(r.get("Water Level (cm)") or r.get("water_level") or 0)
                tip_raw = r.get("Tip Count") or r.get("tip_count")
                tip = float(tip_raw) if tip_raw not in (None, "") else None
                score, status, _ = FuzzyLogic.inference(rain, wl)
                rows.append((ts, tip, rain, wl, round(score, 2), status, 0.0))
            except (ValueError, TypeError):
                continue  # baris rusak dilewati, bukan crash

    rows.sort(key=lambda x: x[0] or "")  # kronologis ascending
    try:
        c.executemany('''
            INSERT INTO sensor_data
                (timestamp, tip_count, rainfall, water_level, fuzzy_score, status, rate_of_change)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', rows)
        conn.commit()
        print(f"[seed] selesai: {len(rows)} record masuk sensor_data")
    except sqlite3.OperationalError as e:
        # DB terkunci worker lain yang seed duluan -> bukan error fatal
        print(f"[seed] dilewati (worker lain sedang seed): {e}")
    finally:
        conn.close()


# ==================== FUZZY LOGIC IMPLEMENT ====================
class FuzzyLogic:
    """Class untuk implementasi Fuzzy Logic (Mamdani, weighted-average defuzz)"""

    @staticmethod
    def fuzzify_rainfall_rendah(x):
        """Membership function: Curah Hujan Rendah"""
        if x <= 10:
            return 1.0
        elif x >= 20:
            return 0.0
        else:
            return (20.0 - x) / 10.0

    @staticmethod
    def fuzzify_rainfall_sedang(x):
        """Membership function: Curah Hujan Sedang"""
        if x <= 15 or x >= 30:
            return 0.0
        elif x < 22.5:
            return (x - 15.0) / 7.5
        else:
            return (30.0 - x) / 7.5

    @staticmethod
    def fuzzify_rainfall_tinggi(x):
        """Membership function: Curah Hujan Tinggi"""
        if x <= 25:
            return 0.0
        elif x >= 35:
            return 1.0
        else:
            return (x - 25.0) / 10.0

    @staticmethod
    def fuzzify_water_normal(x):
        """Membership function: Ketinggian Air Normal"""
        if x <= 100:
            return 1.0
        elif x >= 120:
            return 0.0
        else:
            return (120.0 - x) / 20.0

    @staticmethod
    def fuzzify_water_meningkat(x):
        """Membership function: Ketinggian Air Meningkat"""
        if x <= 110 or x >= 150:
            return 0.0
        elif x < 130:
            return (x - 110.0) / 20.0
        else:
            return (150.0 - x) / 20.0

    @staticmethod
    def fuzzify_water_tinggi(x):
        """Membership function: Ketinggian Air Tinggi"""
        if x <= 140:
            return 0.0
        elif x >= 155:
            return 1.0
        else:
            return (x - 140.0) / 15.0

    @staticmethod
    def inference(rainfall, water_level):
        """
        Fuzzy Inference Engine dengan 9 rules
        Returns: (fuzzy_score, status, rules_fired)
        """
        ch_r = FuzzyLogic.fuzzify_rainfall_rendah(rainfall)
        ch_s = FuzzyLogic.fuzzify_rainfall_sedang(rainfall)
        ch_t = FuzzyLogic.fuzzify_rainfall_tinggi(rainfall)

        kt_n = FuzzyLogic.fuzzify_water_normal(water_level)
        kt_m = FuzzyLogic.fuzzify_water_meningkat(water_level)
        kt_t = FuzzyLogic.fuzzify_water_tinggi(water_level)

        rules = [
            {"alpha": min(ch_r, kt_n), "score": 10, "name": "R1: Rendah-Normal"},
            {"alpha": min(ch_r, kt_m), "score": 35, "name": "R2: Rendah-Meningkat"},
            {"alpha": min(ch_r, kt_t), "score": 60, "name": "R3: Rendah-Tinggi"},
            {"alpha": min(ch_s, kt_n), "score": 40, "name": "R4: Sedang-Normal"},
            {"alpha": min(ch_s, kt_m), "score": 65, "name": "R5: Sedang-Meningkat"},
            {"alpha": min(ch_s, kt_t), "score": 85, "name": "R6: Sedang-Tinggi"},
            {"alpha": min(ch_t, kt_n), "score": 60, "name": "R7: Tinggi-Normal"},
            {"alpha": min(ch_t, kt_m), "score": 85, "name": "R8: Tinggi-Meningkat"},
            {"alpha": min(ch_t, kt_t), "score": 95, "name": "R9: Tinggi-Tinggi"},
        ]

        numerator = sum(rule["alpha"] * rule["score"] for rule in rules)
        denominator = sum(rule["alpha"] for rule in rules)

        fuzzy_score = 10.0 if denominator == 0 else numerator / denominator

        if fuzzy_score <= 25:
            status = "Aman"
        elif fuzzy_score <= 50:
            status = "Waspada"
        elif fuzzy_score <= 75:
            status = "Siaga"
        else:
            status = "Bahaya"

        rules_fired = [rule for rule in rules if rule["alpha"] > 0]
        return fuzzy_score, status, rules_fired


# ==================== HELPER FUNCTIONS ====================
def parse_ts(value):
    """Parse string timestamp SQLite ke datetime, fallback None"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def calculate_rate_of_change(conn, window=10):
    """
    Rate of change (cm/menit) memakai selang waktu SESUNGGUHNYA
    antara pembacaan terbaru vs terlama pada N data terakhir,
    bukan asumsi 10 data = 10 menit.
    """
    c = conn.cursor()
    c.execute('''
        SELECT water_level, timestamp
        FROM sensor_data
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
    ''', (window,))
    data = c.fetchall()

    if len(data) < 2:
        return 0.0

    latest = data[0]
    oldest = data[-1]
    t_new = parse_ts(latest["timestamp"])
    t_old = parse_ts(oldest["timestamp"])
    if t_new is None or t_old is None:
        return 0.0

    minutes = (t_new - t_old).total_seconds() / 60.0
    if minutes <= 0:
        return 0.0

    rate = (latest["water_level"] - oldest["water_level"]) / minutes
    return round(rate, 3)


def check_alert_condition(status, previous_status):
    """Cek apakah perlu kirim alert (status naik level)"""
    alert_levels = {"Aman": 0, "Waspada": 1, "Siaga": 2, "Bahaya": 3}
    return alert_levels.get(status, 0) > alert_levels.get(previous_status, 0)


def save_alert(status, rainfall, water_level):
    """Simpan alert ke database"""
    conn = get_db()
    c = conn.cursor()

    messages = {
        "Waspada": "Peringatan Dini: Kondisi cuaca memburuk. Tetap waspada.",
        "Siaga": "Siaga Banjir: Ketinggian air meningkat signifikan. Bersiaplah untuk evakuasi.",
        "Bahaya": "BAHAYA! Banjir berpotensi terjadi. Segera evakuasi ke tempat aman!",
    }
    message = messages.get(status, "Kondisi normal")

    c.execute('''
        INSERT INTO alerts (status, rainfall, water_level, message)
        VALUES (?, ?, ?, ?)
    ''', (status, rainfall, water_level, message))

    conn.commit()
    conn.close()


# Whitelist kolom sort dari UI -> kolom DB (celah SQL injection tertutup)
SORT_MAP = {
    "timestamp": "timestamp",
    "jumlah_tip": "tip_count",
    "curah_hujan": "rainfall",
    "rainfall": "rainfall",
    "water_level": "water_level",
    "status": "status",
}


def get_ai_flood_prediction(conn):
    """
    Prediksi tingkat mitigasi banjir 3-level berbasis AI (1D-CNN) dengan graceful fallback:
    Level 0: Hijau  (Aman)    - Ketinggian air masih dalam taraf oke
    Level 1: Kuning (Waspada) - Ketinggian air mulai cukup bergejolak
    Level 2: Merah  (Bahaya)  - Ketinggian air melebihi taraf kritis
    """
    c = conn.cursor()
    c.execute('''
        SELECT rainfall, water_level, rate_of_change
        FROM sensor_data
        ORDER BY timestamp DESC, id DESC
        LIMIT 30
    ''')
    rows = c.fetchall()

    if not rows:
        return {
            "level": 0,
            "label": "Hijau (Aman)",
            "color": "green",
            "desc": "Ketinggian air masih dalam taraf yang oke (Stabil)",
            "confidence": 98.0,
            "probabilities": {"Hijau": 98.0, "Kuning": 1.5, "Merah": 0.5},
            "source": "baseline"
        }

    # Coba gunakan model PyTorch 1D-CNN jika torch & checkpoint model_flood_1dcnn.pt ada
    model_path = os.path.join(BASE_DIR, "model_flood_1dcnn.pt")
    if os.path.exists(model_path) and len(rows) >= 15:
        try:
            import torch
            import numpy as np
            from model_cnn import Flood1DCNN
            model = Flood1DCNN(in_channels=3, seq_len=len(rows), num_classes=3)
            model.load_state_dict(torch.load(model_path, map_location="cpu"))
            model.eval()

            seq = [[r["rainfall"], r["water_level"], r["rate_of_change"] or 0.0] for r in reversed(rows)]
            t_in = torch.tensor(seq, dtype=torch.float32).unsqueeze(0).transpose(1, 2)
            with torch.no_grad():
                logits = model(t_in)
                probs = torch.softmax(logits, dim=1).numpy()[0]
                pred_idx = int(np.argmax(probs))

            labels = ["Hijau (Aman)", "Kuning (Waspada)", "Merah (Bahaya)"]
            colors = ["green", "yellow", "red"]
            descs = [
                "Ketinggian air masih dalam taraf yang oke (Stabil)",
                "Ketinggian air mulai cukup bergejolak (Potensi Banjir)",
                "Ketinggian air melebihi taraf kritis (Segera Evakuasi!)"
            ]
            return {
                "level": pred_idx,
                "label": labels[pred_idx],
                "color": colors[pred_idx],
                "desc": descs[pred_idx],
                "confidence": round(float(probs[pred_idx]) * 100, 1),
                "probabilities": {
                    "Hijau": round(float(probs[0]) * 100, 1),
                    "Kuning": round(float(probs[1]) * 100, 1),
                    "Merah": round(float(probs[2]) * 100, 1)
                },
                "source": "1D-CNN (Deep Learning)"
            }
        except Exception:
            pass  # Fallback ke adaptive rolling inference

    # Heuristic rolling inference (fallback mulus sebelum training)
    latest = rows[0]
    wl = latest["water_level"]
    rf = latest["rainfall"]
    roc = latest["rate_of_change"] or 0.0

    if wl >= 150.0 or (wl >= 135.0 and roc > 0.8):
        level = 2
        label = "Merah (Bahaya)"
        color = "red"
        desc = "Ketinggian air melebihi taraf kritis (Segera Evakuasi!)"
        p_merah = min(96.0, 75.0 + (wl - 150.0) * 0.5)
        p_kuning = max(3.0, (100.0 - p_merah) * 0.75)
        p_hijau = max(1.0, 100.0 - p_merah - p_kuning)
    elif wl >= 110.0 or roc > 1.0 or rf >= 20.0:
        level = 1
        label = "Kuning (Waspada)"
        color = "yellow"
        desc = "Ketinggian air mulai cukup bergejolak (Potensi Banjir)"
        p_kuning = min(88.0, 65.0 + (wl - 110.0) * 0.5)
        p_merah = max(5.0, (100.0 - p_kuning) * 0.35)
        p_hijau = max(7.0, 100.0 - p_kuning - p_merah)
    else:
        level = 0
        label = "Hijau (Aman)"
        color = "green"
        desc = "Ketinggian air masih dalam taraf yang oke (Stabil)"
        p_hijau = max(80.0, 95.0 - (wl / 110.0) * 15.0)
        p_kuning = max(4.0, (100.0 - p_hijau) * 0.8)
        p_merah = max(1.0, 100.0 - p_hijau - p_kuning)

    probs = {
        "Hijau": round(p_hijau, 1),
        "Kuning": round(p_kuning, 1),
        "Merah": round(p_merah, 1)
    }
    conf = probs[label.split()[0]]

    return {
        "level": level,
        "label": label,
        "color": color,
        "desc": desc,
        "confidence": conf,
        "probabilities": probs,
        "source": "AI Heuristic Engine (1D-CNN Fallback)"
    }


def row_to_ui(row):
    """Bentuk payload satu baris sesuai kontrak field UI dashboard"""
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "jumlah_tip": row["tip_count"] if row["tip_count"] is not None else 0,
        "curah_hujan": row["rainfall"],
        "water_level": row["water_level"],
        "fuzzy_score": row["fuzzy_score"],
        "label": row["status"],
        "status": row["status"],
        "rate_of_change": row["rate_of_change"],
    }


# ==================== API ENDPOINTS ====================
@app.route('/')
def index():
    """Homepage dengan dashboard"""
    return render_template('index.html')


@app.route('/healthz')
def healthz():
    """Probe siap-servis (Docker healthcheck / load balancer)"""
    return jsonify({"status": "ok", "time": datetime.now().isoformat()}), 200


@app.route('/api/sensor-data', methods=['POST'])
def receive_sensor_data():
    """
    Endpoint menerima data dari ESP32.
    Expected JSON: {"rainfall": float, "water_level": float, "tip_count": float (opsional)}
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Body harus JSON object"}), 400

        try:
            rainfall = float(data.get('rainfall', 0))
            water_level = float(data.get('water_level', 0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "rainfall/water_level harus angka"}), 400

        tip_raw = data.get('tip_count', data.get('jumlah_tip'))
        tip_count = float(tip_raw) if tip_raw not in (None, "") else None

        fuzzy_score, status, rules_fired = FuzzyLogic.inference(rainfall, water_level)

        conn = get_db()
        try:
            rate = calculate_rate_of_change(conn)

            # Auto-upgrade status jika air naik >1 cm/menit (~10 cm/10 menit)
            if rate > 1.0:
                if status == "Aman":
                    status, fuzzy_score = "Waspada", 35.0
                elif status == "Waspada":
                    status, fuzzy_score = "Siaga", 65.0

            c = conn.cursor()
            c.execute('''
                INSERT INTO sensor_data
                    (tip_count, rainfall, water_level, fuzzy_score, status, rate_of_change)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (tip_count, rainfall, water_level, round(fuzzy_score, 2), status, rate))

            c.execute('SELECT status FROM sensor_data ORDER BY timestamp DESC, id DESC LIMIT 2')
            statuses = c.fetchall()
            previous_status = statuses[1]["status"] if len(statuses) > 1 else "Aman"
            conn.commit()
        finally:
            conn.close()

        if check_alert_condition(status, previous_status):
            save_alert(status, rainfall, water_level)

        response = {
            "success": True,
            "data": {
                "rainfall": rainfall,
                "water_level": water_level,
                "tip_count": tip_count,
                "fuzzy_score": round(fuzzy_score, 2),
                "status": status,
                "label": status,
                "rate_of_change": rate,
                "rules_fired": [rule["name"] for rule in rules_fired],
            },
            "timestamp": datetime.now().isoformat(),
        }
        return jsonify(response), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route('/api/latest', methods=['GET'])
def get_latest_data():
    """Get data sensor terbaru (format field lawat)"""
    try:
        conn = get_db()
        row = conn.execute('''
            SELECT timestamp, rainfall, water_level, fuzzy_score, status, rate_of_change
            FROM sensor_data ORDER BY timestamp DESC, id DESC LIMIT 1
        ''').fetchone()
        conn.close()

        if row:
            return jsonify({"success": True, "data": dict(row)}), 200
        return jsonify({"success": False, "message": "No data available"}), 404

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/predict-cnn', methods=['GET'])
def predict_cnn_endpoint():
    """
    Endpoint inferensi 1D-CNN untuk prediksi mitigasi banjir 3 level:
    Hijau (Aman), Kuning (Waspada), Merah (Bahaya)
    """
    try:
        conn = get_db()
        pred = get_ai_flood_prediction(conn)
        conn.close()
        return jsonify({"success": True, "data": pred}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def build_filters(args):
    """Parsing query param filter tabel -> (where_sql, params, order_sql)"""
    where, params = [], []

    start_date = args.get('start_date', '').strip()
    end_date = args.get('end_date', '').strip()
    if start_date:
        where.append("date(timestamp) >= date(?)")
        params.append(start_date)
    if end_date:
        where.append("date(timestamp) <= date(?)")
        params.append(end_date)

    search = args.get('search', '').strip()
    if search:
        where.append("(timestamp LIKE ? OR status LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    sort_col = SORT_MAP.get(args.get('sort', 'timestamp'), "timestamp")
    order = 'ASC' if args.get('order', 'DESC').upper() == 'ASC' else 'DESC'
    order_sql = f"ORDER BY {sort_col} {order}, id {order}"

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params, order_sql


@app.route('/sensor_data', methods=['GET'])
def sensor_data_table():
    """
    Endpoint tabel dashboard.
    Query: page, limit, sort, order, search, start_date, end_date
    Response: { records: [...], pagination: {...}, latest: {...} }
    """
    try:
        page = max(1, request.args.get('page', 1, type=int))
        limit = min(500, max(1, request.args.get('limit', 10, type=int)))
        where_sql, params, order_sql = build_filters(request.args)

        conn = get_db()
        c = conn.cursor()

        total = c.execute(
            f"SELECT COUNT(*) FROM sensor_data {where_sql}", params
        ).fetchone()[0]

        rows = c.execute(f'''
            SELECT id, timestamp, tip_count, rainfall, water_level,
                   fuzzy_score, status, rate_of_change
            FROM sensor_data
            {where_sql}
            {order_sql}
            LIMIT ? OFFSET ?
        ''', (*params, limit, (page - 1) * limit)).fetchall()

        latest_row = c.execute('''
            SELECT id, timestamp, tip_count, rainfall, water_level,
                   fuzzy_score, status, rate_of_change
            FROM sensor_data ORDER BY timestamp DESC, id DESC LIMIT 1
        ''').fetchone()

        ai_pred = get_ai_flood_prediction(conn)
        conn.close()

        return jsonify({
            "records": [row_to_ui(r) for r in rows],
            "pagination": {
                "total_records": total,
                "total_pages": max(1, (total + limit - 1) // limit),
                "current_page": page,
                "per_page": limit,
            },
            "latest": row_to_ui(latest_row) if latest_row else None,
            "ai_prediction": ai_pred,
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/sensor_data/<int:record_id>', methods=['DELETE'])
def delete_sensor_data(record_id):
    """Hapus satu record data sensor"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('DELETE FROM sensor_data WHERE id = ?', (record_id,))
        deleted = c.rowcount
        conn.commit()
        conn.close()

        if deleted == 0:
            return jsonify({"success": False, "message": "Record tidak ditemukan"}), 404
        return jsonify({"success": True, "deleted_id": record_id}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/graph', methods=['GET'])
def graph_data():
    """
    Agregasi time-series untuk Chart.js.
    Query: start_date, end_date, interval (menit), data_type
    Response: {
      rainfall_data:    [{time_interval, total_rainfall}],
      water_level_data: [{time_interval, avg_water_level}]
    }
    """
    try:
        start_date = request.args.get('start_date', '').strip()
        end_date = request.args.get('end_date', '').strip()
        interval_min = min(1440, max(5, request.args.get('interval', 60, type=int)))
        interval_sec = interval_min * 60

        if not start_date or not end_date:
            return jsonify({
                "success": False,
                "error": "start_date dan end_date wajib diisi",
            }), 400

        conn = get_db()
        rows = conn.execute('''
            SELECT
                strftime('%Y-%m-%d %H:%M',
                    (CAST(strftime('%s', timestamp) AS INTEGER) / ?) * ?,
                    'unixepoch'
                ) AS bucket,
                rainfall, water_level
            FROM sensor_data
            WHERE date(timestamp) >= date(?) AND date(timestamp) <= date(?)
              AND timestamp IS NOT NULL
            ORDER BY timestamp ASC
        ''', (interval_sec, interval_sec, start_date, end_date)).fetchall()
        conn.close()

        rain_acc, wl_acc, wl_count = {}, {}, {}
        for r in rows:
            bucket = r["bucket"]
            if bucket is None:
                continue
            rain_acc[bucket] = rain_acc.get(bucket, 0.0) + (r["rainfall"] or 0.0)
            wl_acc[bucket] = wl_acc.get(bucket, 0.0) + (r["water_level"] or 0.0)
            wl_count[bucket] = wl_count.get(bucket, 0) + 1

        buckets = sorted(rain_acc.keys())
        return jsonify({
            "success": True,
            "rainfall_data": [
                {"time_interval": b, "total_rainfall": round(rain_acc[b], 2)} for b in buckets
            ],
            "water_level_data": [
                {"time_interval": b,
                 "avg_water_level": round(wl_acc[b] / wl_count[b], 2) if wl_count[b] else 0}
                for b in buckets
            ],
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/export_csv', methods=['GET'])
def export_csv():
    """Unduh CSV hasil filter (param sama dgn /sensor_data)"""
    try:
        where_sql, params, order_sql = build_filters(request.args)
        limit = request.args.get('limit', 1000, type=int)

        conn = get_db()
        rows = conn.execute(f'''
            SELECT id, timestamp, tip_count, rainfall, water_level, fuzzy_score, status
            FROM sensor_data {where_sql} {order_sql} LIMIT ?
        ''', (*params, limit)).fetchall()
        conn.close()

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "ID", "Timestamp", "Tip Count", "Rainfall (mm)",
            "Water Level (cm)", "Fuzzy Score", "Status",
        ])
        for r in rows:
            writer.writerow([
                r["id"], r["timestamp"], r["tip_count"], r["rainfall"],
                r["water_level"], r["fuzzy_score"], r["status"],
            ])

        filename = f"sensor_data_export_{datetime.now().strftime('%Y-%m-%d')}.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """Get historical data dengan parameter limit"""
    try:
        limit = request.args.get('limit', 100, type=int)
        conn = get_db()
        rows = conn.execute('''
            SELECT timestamp, rainfall, water_level, fuzzy_score, status
            FROM sensor_data ORDER BY timestamp DESC, id DESC LIMIT ?
        ''', (limit,)).fetchall()
        conn.close()

        history = [dict(row) for row in rows]
        return jsonify({"success": True, "data": history}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    """Get alert history"""
    try:
        limit = request.args.get('limit', 50, type=int)
        conn = get_db()
        rows = conn.execute('''
            SELECT timestamp, status, rainfall, water_level, message
            FROM alerts ORDER BY timestamp DESC, id DESC LIMIT ?
        ''', (limit,)).fetchall()
        conn.close()

        return jsonify({"success": True, "data": [dict(row) for row in rows]}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/statistics', methods=['GET'])
def get_statistics():
    """Get statistik dari data yang ada"""
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM sensor_data')
        total_data = c.fetchone()[0]

        c.execute('''
            SELECT status, COUNT(*) as count FROM sensor_data GROUP BY status
        ''')
        status_dist = {row["status"]: row["count"] for row in c.fetchall()}

        c.execute('''
            SELECT AVG(rainfall) AS avg_rainfall,
                   AVG(water_level) AS avg_water_level,
                   MAX(rainfall) AS max_rainfall,
                   MAX(water_level) AS max_water_level
            FROM sensor_data
        ''')
        stats = c.fetchone()
        conn.close()

        response = {
            "total_data": total_data,
            "status_distribution": status_dist,
            "averages": {
                "rainfall": round(stats["avg_rainfall"], 2) if stats["avg_rainfall"] else 0,
                "water_level": round(stats["avg_water_level"], 2) if stats["avg_water_level"] else 0,
            },
            "maximums": {
                "rainfall": round(stats["max_rainfall"], 2) if stats["max_rainfall"] else 0,
                "water_level": round(stats["max_water_level"], 2) if stats["max_water_level"] else 0,
            },
        }
        return jsonify({"success": True, "data": response}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/test-fuzzy', methods=['POST'])
def test_fuzzy():
    """
    Endpoint testing fuzzy logic tanpa save ke database.
    Expected JSON: {"rainfall": float, "water_level": float}
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Body harus JSON object"}), 400

        rainfall = float(data.get('rainfall', 0))
        water_level = float(data.get('water_level', 0))

        fuzzy_score, status, rules_fired = FuzzyLogic.inference(rainfall, water_level)

        return jsonify({
            "success": True,
            "input": {"rainfall": rainfall, "water_level": water_level},
            "output": {"fuzzy_score": round(fuzzy_score, 2), "status": status},
            "rules_fired": [
                {"name": r["name"], "alpha": round(r["alpha"], 3), "score": r["score"]}
                for r in rules_fired
            ],
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# ==================== MAIN ====================
# Init + seed saat module di-import (jalan baik utk `python app.py`
# maupun WSGI server spt gunicorn di dalam Docker)
init_db()
seed_from_csv()

if __name__ == '__main__':
    print("=" * 50)
    print("Flask Flood Monitoring System dengan Fuzzy Logic")
    print("=" * 50)
    print("\nAPI Endpoints:")
    print("  GET    /                 - Dashboard")
    print("  GET    /healthz          - Healthcheck")
    print("  POST   /api/sensor-data  - Terima data dari ESP32")
    print("  GET    /sensor_data      - Tabel dashboard (paginated)")
    print("  DELETE /sensor_data/<id> - Hapus record")
    print("  GET    /graph            - Data grafik time-series")
    print("  GET    /export_csv       - Unduh CSV")
    print("  GET    /api/latest       - Data terbaru (compat)")
    print("  GET    /api/history      - Data historis (compat)")
    print("  GET    /api/alerts       - Alert history")
    print("  GET    /api/statistics   - Statistik data")
    print("  POST   /api/test-fuzzy   - Test fuzzy logic")
    print("\n" + "=" * 50)

    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
