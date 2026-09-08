# Under-Flow Monitoring (HTTP) — FloodSense+
Backend Flask + SQLite + Fuzzy Logic untuk dashboard monitoring banjir
(GEMASTIK XVIII 2025 — Kota Cerdas).

## Struktur
```
app.py               Flask backend (API + dashboard + fuzzy engine)
templates/index.html Dashboard (Chart.js, tabel paginated, grafik, alert)
static/              chart.js lokal, Font Awesome lokal (tahan offline)
data/                data_latih.csv (seed), data_bersih.csv, hasil_klasifikasi.xlsx
ioT/ESP32(HTTP).cpp  Firmware node ESP32 (kirim rainfall, water_level, tip_count)
Dockerfile           Image produksi (gunicorn, non-root, healthcheck)
docker-compose.yml   Service + named volume untuk persist DB
```

## Jalankan Cepat (tanpa Docker)
```bash
python -m venv venv && ./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python app.py            # http://localhost:5000
```
Saat start pertama, tabel kosong → **auto-seed ±26.000 record** dari
`data/data_latih.csv` (fallback `data_bersih.csv`) supaya dashboard &
grafik langsung terisi untuk demo.

## Jalankan via Docker
```bash
docker compose up -d --build        # dashboard di :5000
docker compose logs -f backend
docker compose down
```
- DB ditulis ke `FLOOD_DB_PATH=/app/runtime/flood_monitoring.db`
  → persist di named volume `floodsense_db`, aman terhadap restart.
- Healthcheck periodik ke `/healthz`.
- ESP32 diarahkan ke `http://<IP_HOST>:5000/api/sensor-data`.

## Endpoint
| Method | Path | Fungsi |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/healthz` | Healthcheck container |
| POST | `/api/sensor-data` | Terima data ESP32 `{rainfall, water_level, tip_count?}` |
| GET | `/sensor_data` | Tabel UI: `page, limit, sort, order, search, start_date, end_date` |
| DELETE | `/sensor_data/<id>` | Hapus record |
| GET | `/graph` | Agregasi time-series: `start_date, end_date, interval(mnt)` |
| GET | `/export_csv` | Unduh CSV hasil filter |
| GET | `/api/latest` `/api/history` `/api/alerts` `/api/statistics` | Compat lama |
| POST | `/api/test-fuzzy` | Uji aturan fuzzy tanpa insert DB |

## Catatan Desain
- Path DB di-anchor ke file / env `FLOOD_DB_PATH` — tidak lagi bergantung CWD.
- Rate of change dihitung dari **selang waktu sesungguhnya** antar pembacaan
  (cm/menit), bukan asumsi "10 data = 10 menit". Ambang eskalasi: `> 1 cm/menit`.
- Kolom `tip_count` ditambahkan (migrasi otomatis `ALTER TABLE` utk DB lama).
- Sort kolom pakai whitelist mapping → tertutup dari SQL injection.
- `flask-cors` & `gunicorn` kini terdaftar di `requirements.txt`.
- Aset frontend lokal (chart.js + font awesome + webfonts) → demo tanpa internet aman.
