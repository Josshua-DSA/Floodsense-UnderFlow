"""
FloodSense+ — 1D-CNN Early Warning & Flood Level Prediction Engine
Author: Tim Under-Flow — GEMASTIK XVIII 2025 (Kota Cerdas)

Klasifikasi 3 Level Status:
  0: Hijau  (Aman)    - Ketinggian air normal / aman
  1: Kuning (Waspada) - Ketinggian air mulai bergejolak / laju naik cepat
  2: Merah  (Bahaya)  - Ketinggian air melampaui batas kritis

Pipeline:
  1. Windowing Time-Series: [batch_size, window_size, n_features]
  2. 1D-CNN Feature Extractor: Conv1D -> BatchNorm -> ReLU -> MaxPool1D
  3. Predictor Head: Dense / Linear -> Softmax (3 Kelas)
"""

import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

# ==================== LABELING & DATASET ====================

LABEL_NAMES = {
    0: "Hijau (Aman)",
    1: "Kuning (Waspada)",
    2: "Merah (Bahaya)"
}


def assign_flood_level(water_level, rate_of_change=0.0, rainfall=0.0):
    """
    Logika penentuan ground truth 3 level mitigasi banjir:
    - Merah (2): Air > 150 cm ATAU (Air > 135 cm dan kenaikan cepat)
    - Kuning (1): Air 110-150 cm ATAU laju naik > 1.0 cm/menit ATAU hujan lebat > 20 mm
    - Hijau (0): Sisanya (dalam batas wajar)
    """
    if water_level >= 150.0 or (water_level >= 135.0 and rate_of_change > 0.8):
        return 2  # Merah: Bahaya
    elif water_level >= 110.0 or rate_of_change > 1.0 or rainfall >= 20.0:
        return 1  # Kuning: Waspada
    else:
        return 0  # Hijau: Aman


class FloodTimeSeriesDataset(Dataset):
    """Dataset Sliding Window Time-Series untuk 1D-CNN"""

    def __init__(self, X, y):
        # PyTorch Conv1D mengharapkan tensor format [batch, channels, seq_len]
        # X shape input: [N, seq_len, features] -> transpose ke [N, features, seq_len]
        self.X = torch.tensor(X, dtype=torch.float32).transpose(1, 2)
        self.y = torch.tensor(y, dtype=torch.long)

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


def prepare_sliding_windows(csv_path, window_size=30, forecast_horizon=5):
    """
    Ekstraksi sequence dan label dari dataset CSV.
    - window_size: panjang data historis ke belakang (default 30 timestep)
    - forecast_horizon: jarak prediksi masa depan (t + k) untuk peringatan dini
    """
    df = pd.read_csv(csv_path)

    # Standarisasi nama kolom
    col_map = {
        "Rainfall (mm)": "rainfall",
        "Water Level (cm)": "water_level",
        "Tip Count": "tip_count",
        "Timestamp": "timestamp"
    }
    df = df.rename(columns=col_map)
    df = df.dropna(subset=["rainfall", "water_level"]).reset_index(drop=True)

    # Hitung rate of change sederhana (delta per row)
    df["rate"] = df["water_level"].diff().fillna(0.0)

    features = df[["rainfall", "water_level", "rate"]].values

    # Buat label untuk tiap baris
    raw_labels = np.array([
        assign_flood_level(wl, r, rain)
        for wl, r, rain in zip(df["water_level"], df["rate"], df["rainfall"])
    ])

    X, y = [], []
    total_len = len(df)
    for i in range(total_len - window_size - forecast_horizon + 1):
        # Sequence input dari t sampai t + window_size
        seq = features[i: i + window_size]
        # Label target diambil pada titik masa depan (t + window_size + horizon)
        target = raw_labels[i + window_size + forecast_horizon - 1]
        X.append(seq)
        y.append(target)

    return np.array(X), np.array(y)


# ==================== ARSITEKTUR 1D-CNN ====================

class Flood1DCNN(nn.Module):
    """
    Arsitektur 1D-CNN untuk deteksi pola lonjakan temporal & klasifikasi mitigasi banjir
    """

    def __init__(self, in_channels=3, seq_len=30, num_classes=3):
        super(Flood1DCNN, self).__init__()

        # Blok Konvolusi 1: Ekstraksi fitur tren lokal (derifatif & osilasi)
        self.conv_block1 = nn.Sequential(
            nn.Conv1d(in_channels=in_channels, out_channels=32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2)  # seq_len -> seq_len / 2
        )

        # Blok Konvolusi 2: Ekstraksi pola temporal makro
        self.conv_block2 = nn.Sequential(
            nn.Conv1d(in_channels=32, out_channels=64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2)  # seq_len / 2 -> seq_len / 4
        )

        # Blok Konvolusi 3: Pola badai / akumulasi volume air
        self.conv_block3 = nn.Sequential(
            nn.Conv1d(in_channels=64, out_channels=128, kernel_size=3, padding=1),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1)       # Output channel fixed [batch, 128, 1]
        )

        # Classifier Head
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(p=0.3),
            nn.Linear(64, num_classes)    # Logits 3 kelas
        )

    def forward(self, x):
        x = self.conv_block1(x)
        x = self.conv_block2(x)
        x = self.conv_block3(x)
        logits = self.classifier(x)
        return logits


# ==================== TRAINING & INFERENCE PIPELINE ====================

def train_cnn_model(data_path, epochs=25, batch_size=32, lr=1e-3, save_path="model_flood_1dcnn.pt"):
    """Fungsi modul training 1D-CNN dengan CrossEntropyLoss dan Adam Optimizer"""
    X, y = prepare_sliding_windows(data_path)

    # Split Train 80% / Val 20%
    split_idx = int(0.8 * len(X))
    train_dataset = FloodTimeSeriesDataset(X[:split_idx], y[:split_idx])
    val_dataset = FloodTimeSeriesDataset(X[split_idx:], y[split_idx:])

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = Flood1DCNN(in_channels=3, seq_len=30, num_classes=3).to(device)

    # Penyeimbang bobot kelas bila data Bahaya relatif lebih sedikit
    class_counts = np.bincount(y[:split_idx], minlength=3)
    class_weights = 1.0 / (class_counts + 1e-5)
    class_weights = torch.tensor(class_weights / class_weights.sum(), dtype=torch.float32).to(device)

    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    print(f"[1D-CNN] Mulai Training: {len(train_dataset)} sampel latih, {len(val_dataset)} validasi...")

    best_val_loss = float("inf")
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            outputs = model(X_batch)
            loss = criterion(outputs, y_batch)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(y_batch)

        train_loss /= len(train_dataset)

        # Evaluasi Validasi
        model.eval()
        val_loss, correct = 0.0, 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                outputs = model(X_batch)
                val_loss += criterion(outputs, y_batch).item() * len(y_batch)
                preds = outputs.argmax(dim=1)
                correct += (preds == y_batch).sum().item()

        val_loss /= len(val_dataset)
        val_acc = correct / len(val_dataset)

        print(f"Epoch {epoch:02d}/{epochs} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | Val Acc: {val_acc*100:.2f}%")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), save_path)
            print(f"  --> Checkpoint model tersimpan di: {save_path}")

    return model


def predict_future_level(model, sequence_data, device="cpu"):
    """
    Fungsi inferensi untuk backend/dashboard:
    - sequence_data: array bentuk [window_size, 3] -> (rainfall, water_level, rate)
    Returns: class_id, label_name, probabilities
    """
    model.eval()
    tensor_in = torch.tensor(sequence_data, dtype=torch.float32).unsqueeze(0).transpose(1, 2).to(device)

    with torch.no_grad():
        logits = model(tensor_in)
        probs = torch.softmax(logits, dim=1).cpu().numpy()[0]
        pred_class = int(np.argmax(probs))

    return {
        "class": pred_class,
        "label": LABEL_NAMES[pred_class],
        "confidence": float(probs[pred_class]),
        "probabilities": {
            "Hijau": float(probs[0]),
            "Kuning": float(probs[1]),
            "Merah": float(probs[2])
        }
    }
