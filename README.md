# FloodSense+ (Under-Flow)

[![FloodSense+ CI](https://github.com/Josshua-DSA/Floodsense-UnderFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/Josshua-DSA/Floodsense-UnderFlow/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)
![GEMASTIK](https://img.shields.io/badge/GEMASTIK%20XVIII-Kota%20Cerdas-orange.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

> **FloodSense+ Urban FloodGuard** — Sistem Monitoring & Peringatan Dini Banjir Perkotaan Berbasis IoT, Fuzzy Logic Mamdani, dan Dashboard Web Terintegrasi.  
> Dikembangkan untuk kompetisi **GEMASTIK XVIII 2025 — Divisi Kota Cerdas (Smart City)** oleh **Tim Under-Flow**.

---

## Arsitektur Sistem

```text
[ Sensor Node: ESP32 ]
  ├── Ultrasonic (JSN-SR04T) -> Water Level (cm)
  ├── Tipping Bucket          -> Rainfall Rate & Tips (mm/h)
  ├── DS3231 RTC + SD Card    -> Local Offline Logging
  └── Wi-Fi HTTP Client       -> POST /api/sensor-data
                │
                ▼
[ Backend: Flask REST API + Fuzzy Engine ]
  ├── Fuzzy Logic Mamdani (9 Rules, Defuzzifikasi Weighted-Average)
  ├── Dynamic Rate-of-Change Analysis (cm/menit)
  ├── Auto Alert Escalation
  └── SQLite3 Database (Persistent Volume)
                │
                ▼
[ Web Dashboard: Front-End ]
  ├── Real-time Water & Rainfall Metrics
  ├── Chart.js Time-Series Aggregation (/graph)
  ├── Paginated Table, Sort, Search & Date Filter (/sensor_data)
  ├── CSV Exporter (/export_csv)
  └── Bundled Local Assets (100% Offline-Ready untuk Demo)
```

---

## Struktur Direktori

```text
├── .github/workflows/
│   └── ci.yml                     # GitHub Actions CI (lint, pytest, docker build check)
├── Under-Flow monitoring(HTTP)/
│   ├── app.py                     # Flask API backend & Fuzzy Logic Engine
│   ├── requirements.txt           # Python dependencies
│   ├── Dockerfile                 # Multi-stage production container (gunicorn, non-root)
│   ├── docker-compose.yml         # Container compose with persistent volume
│   ├── .dockerignore              # Clean container build ignore
│   ├── README.md                  # Detail petunjuk modul backend
│   ├── templates/
│   │   └── index.html             # Dashboard UI terintegrasi
│   ├── static/                    # Aset lokal (Chart.js, Font Awesome, Webfonts)
│   ├── data/                      # Dataset historis & seed demo (data_latih.csv, data_bersih.csv)
│   ├── ioT/
│   │   └── ESP32(HTTP).cpp        # Source code firmware ESP32 Arduino/C++
│   ├── tests/
│   │   └── test_app.py            # Automated tests (Pytest)
│   └── Uji model.ipynb            # Notebook riset validasi & tuning membership function
└── .gitignore                     # Filter file runtime, dokumen lomba, dan DB lokal
```

---

## Fitur Utama

1. **Dual Ingestion Endpoint**:
   - Mendukung format JSON baru ESP32 (`rainfall`, `water_level`, `tip_count`).
   - Tetap kompatibel dengan skema legacy tanpa `tip_count`.
2. **Fuzzy Logic Mamdani Teruji**:
   - 3 himpunan curah hujan (Rendah, Sedang, Tinggi).
   - 3 himpunan ketinggian air (Normal, Meningkat, Tinggi).
   - 9 matriks aturan keputusan dengan defuzzifikasi weighted-average menghasilkan skor 0–100 (Aman, Waspada, Siaga, Bahaya).
3. **True Time Rate-of-Change**:
   - Deteksi kenaikan air per menit secara akurat dari interval nyata data, mencegah eskalasi palsu.
4. **Auto-Seed Demo Lomba**:
   - Saat tabel SQLite baru diinisialisasi, sistem otomatis mengisi ±26.000 data historis dari `data_latih.csv` sehingga dashboard, tabel, dan grafik langsung terisi lengkap saat dipresentasikan di depan juri.
5. **Zero External CDN Dependencies**:
   - Seluruh pustaka JS (Chart.js) dan icon (FontAwesome 6 + Webfonts) di-host lokal. Aman berjalan tanpa koneksi internet.

---

## Cara Menjalankan

### Opsi A: Menggunakan Docker (Direkomendasikan)

```bash
cd "Under-Flow monitoring(HTTP)"
docker compose up -d --build
```
Akses dashboard di browser: `http://localhost:5000`  
Untuk melihat log backend:
```bash
docker compose logs -f backend
```
Untuk menghentikan container:
```bash
docker compose down
```

### Opsi B: Menjalankan Lokal (Python Virtual Environment)

```bash
cd "Under-Flow monitoring(HTTP)"
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```
Akses dashboard di browser: `http://localhost:5000`

---

## Menjalankan Unit Test

```bash
cd "Under-Flow monitoring(HTTP)"
pytest tests/ -v
```

Semua 7 skenario uji mencakup healthcheck, UI render, inferensi fuzzy, ingest IoT, alert trigger, pagination tabel, agregasi grafik, dan ekspor CSV.

---

## Ringkasan API

| Method | Path | Keterangan |
|---|---|---|
| `GET` | `/` | Dashboard Web Monitoring |
| `GET` | `/healthz` | Healthcheck Probe |
| `POST` | `/api/sensor-data` | Ingest data IoT dari ESP32 |
| `GET` | `/sensor_data` | Endpoint tabel UI (pagination, search, sort, date filter) |
| `DELETE` | `/sensor_data/<id>` | Hapus record sensor |
| `GET` | `/graph` | Agregasi data time-series untuk grafik |
| `GET` | `/export_csv` | Ekspor CSV data sensor |
| `POST` | `/api/test-fuzzy` | Simulasi uji inferensi fuzzy |
| `GET` | `/api/latest` | Inpeksi data pembacaan paling akhir |
| `GET` | `/api/alerts` | Riwayat catatan peringatan dini |

---

## Tim Pengembang

**Tim Under-Flow** — GEMASTIK XVIII 2025  
Divisi: **Kota Cerdas (Smart City)**  
Politeknik Elektronika Negeri Surabaya (PENS)
