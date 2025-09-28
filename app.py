from flask import Flask, render_template, request
import pandas as pd
import os

app = Flask(__name__)

CSV_FILE = "symptom_disease.csv"  # <-- make sure this file exists next to app.py

# --- Load CSV with safe checks ------------------------------------------------
if not os.path.exists(CSV_FILE):
    raise FileNotFoundError(
        f"CSV file '{CSV_FILE}' not found. Put it in the same folder as app.py."
    )

df = pd.read_csv(CSV_FILE)

# Normalize column names (map several possible variants to canonical names)
cols_lower = {c.strip().lower(): c for c in df.columns}

def find_col(*candidates):
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
    return None

sym_col = find_col("symptom", "symptoms")
dis_col = find_col("disease", "diseases")
prec_col = find_col("precaution", "precautions", "treatment", "possible treatment")
med_col = find_col("medicine", "medicines", "drug", "drugs")

# create canonical columns (if missing, create empty)
df["Symptom"] = df[sym_col] if sym_col else ""
df["Disease"] = df[dis_col] if dis_col else ""
df["Precaution"] = df[prec_col] if prec_col else ""
df["Medicine"] = df[med_col] if med_col else ""

# Build symptom list for autocomplete:
symptom_set = set()
for val in df["Symptom"].dropna().astype(str):
    # allow rows that contain multiple comma-separated symptoms
    for part in val.split(","):
        p = part.strip()
        if p:
            symptom_set.add(p)

symptom_list = sorted(symptom_set, key=lambda x: x.lower())


# --- Routes -------------------------------------------------------------------
@app.route("/")
def index():
    # pass symptom_list to template (autocomplete)
    return render_template("index.html", symptom_list=symptom_list)


@app.route("/result", methods=["POST"])
def result():
    # name="symptoms" in index.html
    typed = request.form.get("symptoms", "") or ""
    typed = typed.strip()
    input_symptoms = [s.strip().lower() for s in typed.split(",") if s.strip()]

    results = {}  # disease -> info dict

    if input_symptoms:
        # iterate rows and find matches
        for _, row in df.iterrows():
            row_sym_raw = str(row["Symptom"])
            row_symptoms = [s.strip().lower() for s in row_sym_raw.split(",") if s.strip()]

            matched = set()
            for ins in input_symptoms:
                for ds in row_symptoms:
                    # allow partial match or exact match (case-insensitive)
                    if ins == ds or ins in ds or ds in ins:
                        matched.add(ds)

            if matched:
                disease_name = str(row["Disease"]).strip()
                if not disease_name:
                    continue
                if disease_name not in results:
                    results[disease_name] = {
                        "matched_symptoms": set(),
                        "total_symptoms": len(row_symptoms),
                        "precautions": str(row["Precaution"]) if pd.notna(row["Precaution"]) else "No information",
                        "medicine": str(row["Medicine"]) if pd.notna(row["Medicine"]) else "No information",
                    }
                results[disease_name]["matched_symptoms"].update(matched)

        # finalize results: convert sets to sorted lists and compute match %
        for disease, info in list(results.items()):
            matched_list = sorted(info["matched_symptoms"])
            info["matched_symptoms"] = matched_list
            # match percent relative to user typed symptoms (so 1 typed symptom matching → 100%)
            info["match_percent"] = round(len(matched_list) / len(input_symptoms) * 100, 2) if input_symptoms else 0

        # sort by match_percent desc (keeping structure as dict for templates that use .items())
        results = dict(sorted(results.items(), key=lambda x: x[1]["match_percent"], reverse=True))

    # render template (results may be empty dict => template will show "no match")
    return render_template("result.html", results=results, typed_symptom=typed)


if __name__ == "__main__":
    app.run(debug=True)
