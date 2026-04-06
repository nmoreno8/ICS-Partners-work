"""
OCR Document Reviewer — Flask backend (v4)
_____________________________________________________________________________________
Works with any financial document JSON (receipts, invoices, purchase orders, etc.).
Predict fields are read dynamically from each JSON file — no hardcoded field list.

New in v4:
  • /api/export-json — exports all corrected predict values into one combined JSON
  • /api/stats       — returns overall + per-file + per-field accuracy breakdown

Usage:
    python3 ui_ocr_v4.py                         # enter folder in UI
    python3 ui_ocr_v4.py /path/to/folder         # pass folder directly

Folder structure required:
    your-folder/
        ui_ocr_v4.py
        templates/
            index.html

File pairs expected in the target folder (same base name):
    DOC001.jpg  +  DOC001_output.json
    INV-042.png +  INV-042_output.json   etc.
"""


import json, csv, sys, copy, base64, io
from pathlib import Path
from flask import Flask, jsonify, request, send_file, render_template


# Config:

TARGET_FOLDER = Path(sys.argv[1]) if len(sys.argv) > 1 else None
IMAGE_EXTS    = {".jpg", ".jpeg", ".png", ".webp"}

# Keys inside "predict" that are metadata, not reviewable fields — these will be ignored in the UI and CSV export:
META_SKIP = {
    "average_conf",
    "corporate_id_valid",
    "has_label",
    "label",
    "doc_type",
}


# Helpers:

def key_to_label(key: str) -> str:
    return " ".join(word.capitalize() for word in key.split("_"))

def extract_predict_keys(predict: dict) -> list:
    return [k for k in predict if k not in META_SKIP]

def val_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)

def scan_folder(folder: Path) -> list:
    pairs = []
    for img in sorted(folder.iterdir()):
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        stem = img.stem
        for candidate in [folder / f"{stem}_output.json", folder / f"{stem}.json"]:
            if candidate.exists():
                pairs.append({"id": stem, "image": str(img), "json": str(candidate)})
                break
    return pairs

def calc_accuracy(field_states: dict) -> float:
    if not field_states:
        return 100.0
    correct = sum(1 for v in field_states.values() if v["correct"])
    return round(correct / len(field_states) * 100, 1)

def build_rows(predict: dict, field_states: dict) -> list:
    rows = []
    for key in extract_predict_keys(predict):
        state = field_states.get(key, {"correct": True, "corrected_value": ""})
        rows.append({
            "key":             key,
            "label":           key_to_label(key),
            "value":           val_to_str(predict.get(key)),
            "correct":         state["correct"],
            "corrected_value": state.get("corrected_value", ""),
        })
    return rows


# Flask app:

app   = Flask(__name__)
STATE = {}

def load_pair(pair: dict) -> dict:
    stem = pair["id"]
    if stem in STATE:
        return STATE[stem]
    data    = json.loads(Path(pair["json"]).read_text(encoding="utf-8"))
    predict = copy.deepcopy(data.get("predict", {}))
    keys    = extract_predict_keys(predict)
    saved_states = data.get("review_states", {})
    field_states = {}
    for key in keys:
        field_states[key] = saved_states.get(key, {"correct": True, "corrected_value": ""})
    STATE[stem] = {
        "data":         data,
        "predict":      predict,
        "predict_keys": keys,
        "field_states": field_states,
        "accuracy":     calc_accuracy(field_states),
        "image_path":   pair["image"],
    }
    return STATE[stem]

def persist(stem: str):
    s = STATE[stem]
    json_path = Path(s["image_path"]).with_name(
        Path(s["image_path"]).stem + "_output.json")
    if not json_path.exists():
        json_path = Path(s["image_path"]).with_suffix(".json")
    s["data"]["review_states"] = s["field_states"]
    json_path.write_text(
        json.dumps(s["data"], ensure_ascii=False, indent=2), encoding="utf-8")


# Routes:

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/folder", methods=["POST"])
def set_folder():
    global TARGET_FOLDER
    body   = request.json
    folder = Path(body.get("folder", "")).expanduser()
    if not folder.is_dir():
        return jsonify({"error": f"Not a valid folder: {folder}"}), 400
    TARGET_FOLDER = folder
    STATE.clear()
    pairs = scan_folder(folder)
    return jsonify({"pairs": pairs, "count": len(pairs)})

@app.route("/api/files")
def list_files():
    if not TARGET_FOLDER:
        return jsonify({"pairs": []})
    pairs  = scan_folder(TARGET_FOLDER)
    result = []
    for p in pairs:
        s = load_pair(p)
        result.append({"id": p["id"], "accuracy": s["accuracy"]})
    return jsonify({"pairs": result})

@app.route("/api/load/<stem>")
def load_file(stem):
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400
    pairs = {p["id"]: p for p in scan_folder(TARGET_FOLDER)}
    if stem not in pairs:
        return jsonify({"error": "Not found"}), 404
    s = load_pair(pairs[stem])
    img_bytes = Path(s["image_path"]).read_bytes()
    ext       = Path(s["image_path"]).suffix.lower().strip(".")
    mime      = "jpeg" if ext in ("jpg", "jpeg") else ext
    img_b64   = base64.b64encode(img_bytes).decode()
    return jsonify({
        "stem":        stem,
        "rows":        build_rows(s["predict"], s["field_states"]),
        "accuracy":    s["accuracy"],
        "conf":        s["predict"].get("average_conf", None),
        "field_count": len(s["predict_keys"]),
        "image":       f"data:image/{mime};base64,{img_b64}",
        "fileName":    s["data"].get("fileName", stem),
    })

@app.route("/api/mark/<stem>", methods=["POST"])
def mark_correct(stem):
    body = request.json
    key  = body.get("key")
    if stem not in STATE:
        return jsonify({"error": "File not loaded"}), 400
    s = STATE[stem]
    s["field_states"][key] = {"correct": True, "corrected_value": ""}
    s["accuracy"] = calc_accuracy(s["field_states"])
    persist(stem)
    return jsonify({"accuracy": s["accuracy"]})

@app.route("/api/save/<stem>", methods=["POST"])
def save_correction(stem):
    body     = request.json
    key      = body.get("key")
    corr_val = body.get("corrected_value", "").strip()
    if stem not in STATE:
        return jsonify({"error": "File not loaded"}), 400
    s = STATE[stem]
    s["field_states"][key] = {"correct": False, "corrected_value": corr_val}
    s["accuracy"] = calc_accuracy(s["field_states"])
    persist(stem)
    return jsonify({"accuracy": s["accuracy"]})

@app.route("/api/export")
def export_csv():
    """Export all reviewed files to one CSV (union of all predict keys)."""
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400
    pairs    = scan_folder(TARGET_FOLDER)
    all_keys, seen = [], set()
    for p in pairs:
        for k in load_pair(p)["predict_keys"]:
            if k not in seen:
                all_keys.append(k); seen.add(k)
    base_cols = ["file", "accuracy", "model_conf", "field_count"]
    out    = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=base_cols + all_keys +
                   [k+"_correct" for k in all_keys] +
                   [k+"_corrected" for k in all_keys],
        extrasaction="ignore",
    )
    writer.writeheader()
    for p in pairs:
        s   = load_pair(p)
        row = {"file": p["id"], "accuracy": s["accuracy"],
               "model_conf": s["predict"].get("average_conf", ""),
               "field_count": len(s["predict_keys"])}
        for key in all_keys:
            row[key] = val_to_str(s["predict"].get(key, ""))
            fs = s["field_states"].get(key, {"correct": True, "corrected_value": ""})
            row[key+"_correct"]   = "YES" if fs["correct"] else "NO"
            row[key+"_corrected"] = fs.get("corrected_value", "")
        writer.writerow(row)
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode("utf-8-sig")),
                     mimetype="text/csv", as_attachment=True,
                     download_name="ocr_benchmark_results.csv")


# Key routes for v4 features:

@app.route("/api/export-json")
def export_json():
    """
    Export one combined JSON file containing every document's final corrected predict values, 
    review decisions, and summary metadata.

    Structure:
    {
      "export_summary": { total_files, overall_accuracy, generated_at },
      "documents": [
        {
          "id": "P0HPJH",
          "file_name": "...",
          "accuracy": 92.3,
          "field_count": 13,
          "fields": {
            "title":        { "predicted": "領収書", "correct": true,  "corrected_value": "" },
            "total":        { "predicted": "1708",  "correct": false, "corrected_value": "1710" },
            ...
          }
        },
        ...
      ]
    }
    """
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400

    from datetime import datetime
    pairs = scan_folder(TARGET_FOLDER)
    documents = []
    accuracy_sum = 0.0

    for p in pairs:
        s = load_pair(p)
        fields = {}
        for key in s["predict_keys"]:
            fs = s["field_states"].get(key, {"correct": True, "corrected_value": ""})
            fields[key] = {
                "predicted":       val_to_str(s["predict"].get(key, "")),
                "correct":         fs["correct"],
                "corrected_value": fs.get("corrected_value", ""),
                # Final value = corrected_value if marked wrong, else predicted
                "final_value": fs.get("corrected_value", "") if not fs["correct"]
                               else val_to_str(s["predict"].get(key, "")),
            }
        documents.append({
            "id":          p["id"],
            "file_name":   s["data"].get("fileName", p["id"]),
            "accuracy":    s["accuracy"],
            "field_count": len(s["predict_keys"]),
            "model_conf":  s["predict"].get("average_conf", None),
            "fields":      fields,
        })
        accuracy_sum += s["accuracy"]

    overall = round(accuracy_sum / len(documents), 1) if documents else 100.0
    combined = {
        "export_summary": {
            "total_files":      len(documents),
            "overall_accuracy": overall,
            "generated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "documents": documents,
    }

    out = io.BytesIO(json.dumps(combined, ensure_ascii=False, indent=2).encode("utf-8"))
    out.seek(0)
    return send_file(out, mimetype="application/json", as_attachment=True,
                     download_name="ocr_corrected_results.json")

@app.route("/api/stats")
def get_stats():
    """
    Returns accuracy statistics across all loaded files:
      - overall_accuracy    : mean of per-file accuracies
      - total_files         : number of file pairs found
      - reviewed_files      : files that have had at least one verdict set
      - per_file            : [ { id, accuracy, field_count, correct, incorrect } ]
      - per_field           : [ { key, label, appearances, correct_count, accuracy } ]
                              sorted by accuracy ascending (worst first)
    """
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400

    pairs = scan_folder(TARGET_FOLDER)
    per_file  = []
    # key -> {appearances, correct_count}
    field_agg: dict = {}

    accuracy_sum = 0.0
    reviewed = 0

    for p in pairs:
        s = load_pair(p)
        correct   = sum(1 for v in s["field_states"].values() if v["correct"])
        incorrect = len(s["field_states"]) - correct
        any_verdict = any(
            k in s["data"].get("review_states", {})
            for k in s["predict_keys"]
        )
        if any_verdict:
            reviewed += 1

        per_file.append({
            "id":          p["id"],
            "accuracy":    s["accuracy"],
            "field_count": len(s["predict_keys"]),
            "correct":     correct,
            "incorrect":   incorrect,
        })
        accuracy_sum += s["accuracy"]

        for key in s["predict_keys"]:
            if key not in field_agg:
                field_agg[key] = {"appearances": 0, "correct_count": 0}
            field_agg[key]["appearances"] += 1
            fs = s["field_states"].get(key, {"correct": True})
            if fs["correct"]:
                field_agg[key]["correct_count"] += 1

    overall = round(accuracy_sum / len(pairs), 1) if pairs else 100.0

    per_field = sorted([
        {
            "key":           k,
            "label":         key_to_label(k),
            "appearances":   v["appearances"],
            "correct_count": v["correct_count"],
            "accuracy":      round(v["correct_count"] / v["appearances"] * 100, 1),
        }
        for k, v in field_agg.items()
    ], key=lambda x: x["accuracy"])  # worst first

    return jsonify({
        "overall_accuracy": overall,
        "total_files":      len(pairs),
        "reviewed_files":   reviewed,
        "per_file":         per_file,
        "per_field":        per_field,
    })


#  Entry point:

if __name__ == "__main__":
    import webbrowser, threading
    port = 5050

    if TARGET_FOLDER and not TARGET_FOLDER.is_dir():
        print(f"  Folder not found: {TARGET_FOLDER}")
        TARGET_FOLDER = None

    print(f"\n  OCR Document Reviewer  v4")
    print(f"  ────────────────────────────────────────────────────────")
    if TARGET_FOLDER:
        print(f"  Folder : {TARGET_FOLDER}")
        pairs = scan_folder(TARGET_FOLDER)
        print(f"  Files  : {len(pairs)} document pair(s) found")
    print(f"  URL    : http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")

    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(port=port, debug=False)