/************************************************************
 * ESP32 FloodSense – Single Node (HTTP)
 * Fitur: RTC + SD + Tipping Bucket + Ultrasonic + HTTP POST
 * Kirim ke Flask: {"rainfall": <mm/h>, "water_level": <cm>}
 * (Opsional) Bisa kirim format string ala PA: "Dt:...,Tm:...,Hj:...,Tg:..."
 ************************************************************/
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include "RTClib.h"

// ================== KONFIG ==================
const char* WIFI_SSID   = "GANTI_SSID";
const char* WIFI_PASS   = "GANTI_PASSWORD";
// Contoh Flask VPS: "https://api.domainmu.com/api/sensor-data"
const char* BACKEND_URL = "http://192.168.1.100:5000/api/sensor-data";
const bool  USE_TLS     = false;        // true bila https (produksi: pakai root CA)
const char* API_KEY     = "";           // opsional: isi jika backend cek X-API-Key
const unsigned long POST_INTERVAL_MS = 10UL * 1000UL; // kirim tiap 10 detik

// ================== PIN ==================
#define TIP_PIN     27       // tipping bucket (reed switch)
#define US_TRIG     25       // ultrasonic trigger
#define US_ECHO     33       // ultrasonic echo (ingat: echo JSN-SR04T = 5V → pakai divider)
#define SD_CS        5       // SD card CS (VSPI default: SCK18, MISO19, MOSI23)
#define LED_PIN      2       // LED indikator (onboard)

// ================== PARAMETER SENSOR ==================
const float BUCKET_MM = 1.346f;   // 1 tip = 1.346 mm (sesuai PA)
const float H_SENSOR_KE_DASAR_CM = 200.0f;         // ukur di lapangan!
const float SOUND_SPEED_CM_PER_US = 0.0343f;       // cm/us (sebelum /2 echo)
const uint8_t ULTRA_SAMPLES = 5;

// ================== GLOBALS ==================
RTC_DS3231 rtc;
File sdfile;

volatile uint32_t tipCountTotal = 0;
volatile uint32_t tipCountThisSecond = 0;
volatile uint32_t lastTipMs = 0;

uint16_t ring60[60];  // tips per detik (window 60 s)
uint8_t  ringIdx = 0;
unsigned long lastSecondTick = 0;
unsigned long lastPostMs     = 0;

// ================== ISR ==================
void IRAM_ATTR tipISR() {
  uint32_t now = millis();
  if (now - lastTipMs > 50) {     // debounce 50 ms
    tipCountTotal++;
    tipCountThisSecond++;
    lastTipMs = now;
  }
}

// ================== UTIL WAKTU ==================
void nowStrings(char* dt, size_t dsz, char* tm, size_t tsz) {
  DateTime n = rtc.now();
  snprintf(dt, dsz, "%04d-%02d-%02d", n.year(), n.month(), n.day());
  snprintf(tm, tsz, "%02d:%02d:%02d", n.hour(), n.minute(), n.second());
}

// ================== RAINFALL ==================
void rollOneSecond() {
  uint16_t tips = tipCountThisSecond;
  tipCountThisSecond = 0;
  ringIdx = (ringIdx + 1) % 60;
  ring60[ringIdx] = tips;
}

float rainfallRateMMH() {
  uint32_t tips60 = 0;
  for (int i = 0; i < 60; i++) tips60 += ring60[i];
  return (float)tips60 * BUCKET_MM * 60.0f;  // mm pada 60 s → mm/h
}

float rainfallTotalMM() {
  return (float)tipCountTotal * BUCKET_MM;
}

// jumlah tip dalam window 60 s terakhir (sinkron dgn rainfallRateMMH)
uint32_t tipCountWindow() {
  uint32_t tips60 = 0;
  for (int i = 0; i < 60; i++) tips60 += ring60[i];
  return tips60;
}

// ================== ULTRASONIC ==================
float readWaterLevelCM() {
  float acc = 0; uint8_t good = 0;
  for (uint8_t i = 0; i < ULTRA_SAMPLES; i++) {
    digitalWrite(US_TRIG, LOW); delayMicroseconds(3);
    digitalWrite(US_TRIG, HIGH); delayMicroseconds(10);
    digitalWrite(US_TRIG, LOW);
    unsigned long dur = pulseIn(US_ECHO, HIGH, 35000UL); // 35 ms ~ 6 m
    if (dur > 0) {
      float dist = (dur * SOUND_SPEED_CM_PER_US) / 2.0f; // bolak-balik → /2
      acc += dist; good++;
    }
    delay(30);
  }
  if (!good) return NAN;
  float jarak_cm = acc / good;
  float tinggi_air = H_SENSOR_KE_DASAR_CM - jarak_cm;
  if (tinggi_air < 0) tinggi_air = 0;
  return tinggi_air;
}

// ================== SD LOG ==================
void sd_log(const String& line) {
  sdfile = SD.open("/node.csv", FILE_APPEND);
  if (sdfile) { sdfile.println(line); sdfile.close(); }
}

// ================== WIFI/HTTP ==================
void wifiConnect() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] Connecting");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    Serial.print(".");
    delay(300);
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " FAIL");
}

bool postFlaskJSON(float rr_mmh, float wl_cm, uint32_t tips) {
  wifiConnect();
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  if (USE_TLS) {
    WiFiClientSecure client; client.setInsecure();   // cepat; produksi: root CA/pinning
    http.begin(client, BACKEND_URL);
  } else {
    http.begin(BACKEND_URL);
  }
  http.addHeader("Content-Type", "application/json");
  if (API_KEY && strlen(API_KEY)>0) http.addHeader("X-API-Key", API_KEY);

  String json = String("{\"rainfall\":") + String(rr_mmh, 2) +
                ",\"water_level\":" + String(wl_cm, 2) +
                ",\"tip_count\":" + String(tips) + "}";
  int code = http.POST(json);
  String resp = http.getString();
  http.end();

  Serial.printf("[POST] %d %s\n", code, resp.c_str());
  return (code >= 200 && code < 300);
}

// (Opsional) kirim gaya “PA” (string Dt/Tm/Hj/Tg/… ke Apps Script)
// Ubah BACKEND_URL ke URL Apps Script dan Content-Type sesuai jika perlu.
// bool postPAString(const String& msg) { ... }

// ================== SETUP ==================
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // RTC
  Wire.begin();
  if (!rtc.begin()) Serial.println("[RTC] Tidak terdeteksi!");
  if (rtc.lostPower()) {
    // set waktu awal (sekali saja); idealnya sinkron via Serial/BT/NTP
    rtc.adjust(DateTime(2025, 10, 4, 12, 0, 0));
  }

  // SD
  if (!SD.begin(SD_CS)) Serial.println("[SD] GAGAL. Cek wiring/CS.");

  // Tipping bucket
  pinMode(TIP_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TIP_PIN), tipISR, FALLING);

  // Ultrasonik
  pinMode(US_TRIG, OUTPUT);
  pinMode(US_ECHO, INPUT);

  // init buffer hujan
  for (int i = 0; i < 60; i++) ring60[i] = 0;
  lastSecondTick = millis();

  wifiConnect();
  Serial.println("[INIT] Selesai.");
}

// ================== LOOP ==================
void loop() {
  // tick 1 detik untuk window 60 s
  if (millis() - lastSecondTick >= 1000) {
    lastSecondTick += 1000;
    rollOneSecond();
  }

  // baca sensor
  float wl_cm  = readWaterLevelCM();
  float rr_mmh = rainfallRateMMH();

  // cetak & log
  char dt[11], tm[9]; nowStrings(dt, sizeof(dt), tm, sizeof(tm));
  Serial.printf("[DATA] %s %s | rain=%.1f mm/h | total=%.2f mm | level=%.1f cm\n",
                dt, tm, rr_mmh, rainfallTotalMM(), wl_cm);

  // log ke SD (CSV): dt,tm,mmh,mm_total,wl
  String csv = String(dt) + "," + String(tm) + "," +
               String(rr_mmh,1) + "," + String(rainfallTotalMM(),2) + "," +
               String(wl_cm,1);
  sd_log(csv);

  // kirim berkala
  if (millis() - lastPostMs >= POST_INTERVAL_MS) {
    lastPostMs = millis();
    if (!isnan(wl_cm)) {
      bool ok = postFlaskJSON(rr_mmh, wl_cm, tipCountWindow());
      digitalWrite(LED_PIN, ok ? HIGH : LOW);
    } else {
      Serial.println("[WARN] Ultrasonic NaN – tidak dikirim.");
      digitalWrite(LED_PIN, LOW);
    }
  }

  delay(80);
}
