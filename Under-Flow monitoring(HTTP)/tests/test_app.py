import os
import sys
import pytest

# Pastikan import app.py dari parent directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


@pytest.fixture
def client(tmp_path):
    """Fixture test client Flask dengan database isolasi di tmp_path"""
    test_db = str(tmp_path / "test_flood.db")
    os.environ["FLOOD_DB_PATH"] = test_db

    import app as flood_app

    flood_app.init_db()
    flood_app.seed_from_csv()

    with flood_app.app.test_client() as client:
        yield client


def test_healthz(client):
    """Healthcheck endpoint harus 200 OK"""
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_index_html(client):
    """Dashboard UI harus render sukses dan memuat aset lokal"""
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "static/js/chart.js" in html
    assert "cdn.jsdelivr" not in html


def test_fuzzy_inference(client):
    """Uji inferensi fuzzy logic tanpa save DB"""
    res = client.post(
        "/api/test-fuzzy",
        json={"rainfall": 35.0, "water_level": 160.0},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["output"]["status"] == "Bahaya"
    assert data["output"]["fuzzy_score"] >= 80.0


def test_receive_sensor_data_and_alert(client):
    """Uji ingest data sensor dari ESP32 dan trigger alert saat status naik"""
    res = client.post(
        "/api/sensor-data",
        json={"rainfall": 40.0, "water_level": 170.0, "tip_count": 15},
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "Bahaya"
    assert payload["data"]["tip_count"] == 15

    # Cek alert tercatat
    alert_res = client.get("/api/alerts")
    assert alert_res.status_code == 200
    assert len(alert_res.get_json()["data"]) >= 1


def test_sensor_data_table_and_pagination(client):
    """Uji endpoint tabel UI: pagination, sort, dan field contract"""
    res = client.get("/sensor_data?page=1&limit=5&sort=curah_hujan&order=DESC")
    assert res.status_code == 200
    json_data = res.get_json()
    assert "records" in json_data
    assert "pagination" in json_data
    assert len(json_data["records"]) <= 5
    if json_data["records"]:
        rec = json_data["records"][0]
        for field in ["id", "timestamp", "jumlah_tip", "curah_hujan", "water_level", "label", "status"]:
            assert field in rec


def test_graph_aggregation(client):
    """Uji endpoint agregasi grafik /graph"""
    res = client.get("/graph?start_date=2025-01-01&end_date=2025-12-31&interval=60")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "rainfall_data" in data
    assert "water_level_data" in data


def test_export_csv(client):
    """Uji endpoint ekspor CSV"""
    res = client.get("/export_csv?limit=10")
    assert res.status_code == 200
    assert "text/csv" in res.headers["Content-Type"]
    content = res.get_data(as_text=True)
    assert content.startswith("ID,Timestamp,Tip Count,Rainfall (mm)")
