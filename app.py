from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
import pandas as pd
import sqlite3
import os
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
app.secret_key = "replace_this_with_a_real_secret_in_production"

CSV_FILE = "symptom_disease.csv"
DB_FILE = "users.db"

# ------------------ Load CSV and normalize ------------------
if not os.path.exists(CSV_FILE):
    raise FileNotFoundError(f"CSV file '{CSV_FILE}' not found in project folder.")

df = pd.read_csv(CSV_FILE)

# Map several possible column names to canonical names
cols_lower = {c.strip().lower(): c for c in df.columns}

def find_col(*candidates):
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
    return None

sym_col = find_col("symptom", "symptoms")
dis_col = find_col("disease", "diseases")
prec_col = find_col("precaution", "precautions", "treatment")
med_col = find_col("medicine", "medicines", "drug", "drugs")

# Create canonical columns (as strings)
df["Symptom"] = df[sym_col].astype(str) if sym_col else ""
df["Disease"] = df[dis_col].astype(str) if dis_col else ""
df["Precaution"] = df[prec_col].astype(str) if prec_col else ""
df["Medicine"] = df[med_col].astype(str) if med_col else ""

# Build a data structure: disease -> {symptoms:set, precautions:set, medicines:set}
disease_map = {}
for _, row in df.iterrows():
    disease = str(row["Disease"]).strip()
    if not disease:
        continue
    if disease not in disease_map:
        disease_map[disease] = {"symptoms": set(), "precautions": set(), "medicines": set()}

    # symptoms may be comma-separated in cell
    for s in str(row["Symptom"]).split(","):
        s = s.strip()
        if s:
            disease_map[disease]["symptoms"].add(s)

    prec = str(row["Precaution"]).strip()
    if prec and prec.lower() != "nan":
        disease_map[disease]["precautions"].add(prec)

    med = str(row["Medicine"]).strip()
    if med and med.lower() != "nan":
        disease_map[disease]["medicines"].add(med)

# Symptom list for autocomplete (unique)
symptom_list = sorted({s for info in disease_map.values() for s in info["symptoms"]}, key=lambda x: x.lower())

# ------------------ Database helpers ------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    # reports table
    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            symptoms TEXT,
            disease TEXT,
            precaution TEXT,
            medicine TEXT,
            match_percent REAL,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_report(username, symptoms_text, disease, precaution, medicine, match_percent):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reports (username, symptoms, disease, precaution, medicine, match_percent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (username, symptoms_text, disease, precaution, medicine, match_percent, datetime.utcnow().isoformat())
    )
    report_id = c.lastrowid
    conn.commit()
    conn.close()
    return report_id

# Ensure DB exists
init_db()

# ------------------ Routes ------------------
@app.route("/")
def index():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("index.html", username=session.get("username"), symptom_list=symptom_list)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password_raw = request.form.get("password", "")
        if not username or not password_raw:
            flash("Please provide username and password", "danger")
            return redirect(url_for("register"))
        password_hashed = generate_password_hash(password_raw)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password_hashed))
            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")
            return redirect(url_for("register"))
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, password FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row[1], password):
            session["username"] = username
            session["user_id"] = row[0]
            flash(f"Welcome, {username}!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid credentials", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out", "info")
    return redirect(url_for("login"))

@app.route("/submit", methods=["POST"])
def submit():
    if "username" not in session:
        return redirect(url_for("login"))

    typed = request.form.get("symptoms", "").strip()
    input_symptoms = [s.strip().lower() for s in typed.split(",") if s.strip()]

    results = {}
    if input_symptoms:
        for disease, info in disease_map.items():
            disease_symptoms = list(info["symptoms"])
            disease_symptoms_lower = [s.lower() for s in disease_symptoms]

            matched = set()
            for user_sym in input_symptoms:
                for idx, ds_lower in enumerate(disease_symptoms_lower):
                    if user_sym == ds_lower or user_sym in ds_lower or ds_lower in user_sym:
                        matched.add(disease_symptoms[idx])

            if matched:
                matched_list = sorted(matched)
                match_percent = round(len(matched_list) / len(input_symptoms) * 100, 2) if input_symptoms else 0.0
                precaution = next(iter(info["precautions"]), "No information")
                medicine = next(iter(info["medicines"]), "No information")
                results[disease] = {
                    "matched_symptoms": matched_list,
                    "total_symptoms": len(disease_symptoms),
                    "match_percent": match_percent,
                    "precaution": precaution,
                    "medicine": medicine
                }

    results = dict(sorted(results.items(), key=lambda x: x[1]["match_percent"], reverse=True))

    report_id = None
    if results:
        best_disease, best_info = next(iter(results.items()))
        report_id = save_report(
            session.get("username"),
            typed,
            best_disease,
            best_info.get("precaution", ""),
            best_info.get("medicine", ""),
            best_info.get("match_percent", 0.0)
        )

    return render_template("result.html", results=results, typed_symptom=typed, report_id=report_id)

@app.route("/history")
def history():
    if "username" not in session:
        return redirect(url_for("login"))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, symptoms, disease, precaution, medicine, match_percent, created_at FROM reports WHERE username=? ORDER BY id DESC", (session.get("username"),))
    rows = c.fetchall()
    conn.close()
    return render_template("history.html", rows=rows)

# ------------------ PDF download using ReportLab ------------------
@app.route("/download/<int:report_id>")
def download(report_id):
    if "username" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT symptoms, disease, precaution, medicine, match_percent, created_at, username FROM reports WHERE id=?", (report_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        flash("Report not found", "danger")
        return redirect(url_for("history"))

    if row[6] != session.get("username"):
        flash("You are not authorized to download this report.", "danger")
        return redirect(url_for("history"))

    symptoms_text, disease, precaution, medicine, match_percent, created_at, _ = row

    buffer = io.BytesIO()
    c_pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Title
    c_pdf.setFont("Helvetica-Bold", 16)
    c_pdf.drawCentredString(width / 2, height - 2*cm, "Symptom Diagnosis Report 🩺")

    # Date
    c_pdf.setFont("Helvetica", 12)
    c_pdf.drawString(2*cm, height - 3*cm, f"Generated: {created_at}")

    y_position = height - 4*cm

    # Symptoms
    text = f"Symptoms: {symptoms_text}"
    for line in text.split("\n"):
        c_pdf.drawString(2*cm, y_position, line)
        y_position -= 0.7*cm

    # Disease
    text = f"Disease: {disease}"
    for line in text.split("\n"):
        c_pdf.drawString(2*cm, y_position, line)
        y_position -= 0.7*cm

    # Precaution
    text = f"Precaution: {precaution}"
    for line in text.split("\n"):
        c_pdf.drawString(2*cm, y_position, line)
        y_position -= 0.7*cm

    # Medicine
    text = f"Medicine: {medicine}"
    for line in text.split("\n"):
        c_pdf.drawString(2*cm, y_position, line)
        y_position -= 0.7*cm

    # Match %
    text = f"Match %: {match_percent}%"
    c_pdf.drawString(2*cm, y_position, text)

    c_pdf.showPage()
    c_pdf.save()

    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"report_{report_id}.pdf", mimetype="application/pdf")

# ------------------ Run ------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

