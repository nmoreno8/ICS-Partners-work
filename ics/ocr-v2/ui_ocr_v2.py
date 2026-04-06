"""
OCR Receipt Reviewer — Flask Web UI
____________________________________________________________________________

Usage:
    python3 ui_ocr_v2.py                         # prompts for folder
    python3 ui_ocr_v2.py /path/to/receipts       # pass folder directly

Expects pairs of files in the target folder:
    P0HPJH.jpg  +  P0HPJH_output.json
    ABC123.png  +  ABC123_output.json   etc.
"""


import json, csv, re, sys, os, copy, base64, io
from pathlib import Path
from difflib import SequenceMatcher
from flask import Flask, jsonify, request, send_file


# Configuration:
TARGET_FOLDER = Path(sys.argv[1]) if len(sys.argv) > 1 else None
IMAGE_EXTS    = {".jpg", ".jpeg", ".png", ".webp"}
NOT_FOUND     = {"NOT_FOUND", "NOT_FOUND_ZIP"}

FIELD_META = {
    "title":         {"label": "Title / Doc Type",       "hint": r"領収"},
    "corporate_id":  {"label": "Corporate / Tax ID",     "hint": r"T?\d{10,13}"},
    "company":       {"label": "Company Name",           "hint": r"株式会社|イオン"},
    "total":         {"label": "Total Amount (¥)",       "hint": r"合計"},
    "subtotal":      {"label": "Sub-Total (¥)",          "hint": r"小計"},
    "deposit":       {"label": "Deposit / Cash (¥)",     "hint": r"預り|お預"},
    "eight_pct_tax": {"label": "8% Tax Base (¥)",        "hint": r"外税8"},
    "ten_pct_tax":   {"label": "10% Tax Base (¥)",       "hint": r"外税10|10%"},
    "change_amnt":   {"label": "Change (¥)",             "hint": r"お釣り"},
    "date":          {"label": "Date",                   "hint": r"\d{4}[年/]\d{1,2}[月/]\d{1,2}"},
    "time":          {"label": "Time",                   "hint": r"\d{1,2}:\d{2}"},
    "zip":           {"label": "Zip Code",               "hint": r"\d{3}-\d{4}"},
    "tel":           {"label": "Telephone",              "hint": r"\d{2,4}-\d{3,4}-\d{3,4}"},
}


# Scoring logic :

def _clean(s):
    return re.sub(r"[\s¥,￥　]", "", str(s).lower())

def value_in_text(value, all_texts):
    if not value or value in NOT_FOUND:
        return False
    return _clean(value) in _clean(all_texts)

def fuzzy(a, b):
    return SequenceMatcher(None, _clean(a), _clean(b)).ratio()

def evaluate_field(key, raw_value, all_texts):
    values = raw_value if isinstance(raw_value, list) else [raw_value]
    all_nf = all(v in NOT_FOUND for v in values)
    hint   = FIELD_META.get(key, {}).get("hint", "")

    if all_nf:
        if hint and re.search(hint, all_texts):
            return {"status": "SUSPECT",    "score": 0.0, "note": "NOT_FOUND but pattern detected in receipt"}
        return     {"status": "NOT_FOUND",  "score": 1.0, "note": "Confirmed absent"}

    for v in values:
        if v in NOT_FOUND:
            continue
        if value_in_text(v, all_texts):
            return {"status": "CORRECT",    "score": 1.0, "note": "Confirmed in receipt text"}
    best = max((fuzzy(v, all_texts) for v in values if v not in NOT_FOUND), default=0)
    if best >= 0.6:
        return     {"status": "MISMATCH",   "score": best, "note": f"Partial match ({best:.0%}) — format diff?"}
    return         {"status": "MISMATCH",   "score": best, "note": "Value not found in receipt text"}

def benchmark(predict, all_texts):
    rows, total = [], 0.0
    for key, meta in FIELD_META.items():
        raw = predict.get(key)
        if raw is None:
            ev = {"status": "MISSING", "score": 0.0, "note": "Key absent"}
        else:
            ev = evaluate_field(key, raw, all_texts)
        ev["key"]   = key
        ev["label"] = meta["label"]
        ev["value"] = raw
        total += ev["score"]
        rows.append(ev)
    accuracy = round(total / len(FIELD_META) * 100, 1)
    return rows, accuracy


# File scanner:

def scan_folder(folder: Path):
    pairs = []
    for img in sorted(folder.iterdir()):
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        stem = img.stem
        # Accept both stem_output.json and stem.json
        for candidate in [folder / f"{stem}_output.json", folder / f"{stem}.json"]:
            if candidate.exists():
                pairs.append({"id": stem, "image": str(img), "json": str(candidate)})
                break
    return pairs


# Flask application:

app = Flask(__name__)
STATE = {}   # in-memory store: stem -> {data, corrected_predict, rows, accuracy}

def load_pair(pair):
    stem = pair["id"]
    if stem in STATE:
        return STATE[stem]
    data     = json.loads(Path(pair["json"]).read_text(encoding="utf-8"))
    predict  = copy.deepcopy(data.get("predict", {}))
    all_texts= data.get("all_texts", "")
    rows, acc = benchmark(predict, all_texts)
    STATE[stem] = {
        "data": data, "predict": predict,
        "all_texts": all_texts, "rows": rows, "accuracy": acc,
        "image_path": pair["image"]
    }
    return STATE[stem]


# Routes:

@app.route("/")
def index():
    return HTML_PAGE

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
        return jsonify({"error": "No folder set"}), 400
    pairs = scan_folder(TARGET_FOLDER)
    return jsonify({"pairs": pairs})

@app.route("/api/load/<stem>")
def load_file(stem):
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400
    pairs = {p["id"]: p for p in scan_folder(TARGET_FOLDER)}
    if stem not in pairs:
        return jsonify({"error": "Not found"}), 404
    s = load_pair(pairs[stem])
    # Encode image as base64
    img_bytes  = Path(s["image_path"]).read_bytes()
    ext        = Path(s["image_path"]).suffix.lower().strip(".")
    mime       = "jpeg" if ext in ("jpg","jpeg") else ext
    img_b64    = base64.b64encode(img_bytes).decode()
    return jsonify({
        "stem": stem,
        "predict": s["predict"],
        "all_texts": s["all_texts"],
        "text_blocks": s["data"].get("text_blocks", {}),
        "rows": s["rows"],
        "accuracy": s["accuracy"],
        "conf": s["predict"].get("average_conf", 0),
        "image": f"data:image/{mime};base64,{img_b64}",
        "fileName": s["data"].get("fileName", stem)
    })

@app.route("/api/save/<stem>", methods=["POST"])
def save_correction(stem):
    body = request.json
    key  = body.get("key")
    val  = body.get("value")
    if stem not in STATE:
        return jsonify({"error": "File not loaded"}), 400
    s = STATE[stem]
    orig = s["predict"].get(key)
    s["predict"][key] = [val] if isinstance(orig, list) else val
    rows, acc = benchmark(s["predict"], s["all_texts"])
    s["rows"], s["accuracy"] = rows, acc
    # Persist back to JSON
    s["data"]["predict"] = s["predict"]
    json_path = Path(s["image_path"]).with_name(
        Path(s["image_path"]).stem + "_output.json")
    if not json_path.exists():
        json_path = Path(s["image_path"]).with_suffix(".json")
    json_path.write_text(json.dumps(s["data"], ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"rows": rows, "accuracy": acc})

@app.route("/api/export")
def export_csv():
    if not TARGET_FOLDER:
        return jsonify({"error": "No folder set"}), 400
    pairs = scan_folder(TARGET_FOLDER)
    out   = io.StringIO()
    fieldnames = ["file", "accuracy", "model_conf"] + list(FIELD_META.keys()) + \
                 [k+"_status" for k in FIELD_META.keys()]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for p in pairs:
        s   = load_pair(p)
        row = {"file": p["id"],
               "accuracy": s["accuracy"],
               "model_conf": s["predict"].get("average_conf", "")}
        for r in s["rows"]:
            v = r["value"]
            row[r["key"]]          = ", ".join(v) if isinstance(v, list) else (v or "")
            row[r["key"]+"_status"]= r["status"]
        writer.writerow(row)
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode()),
                     mimetype="text/csv",
                     as_attachment=True,
                     download_name="ocr_benchmark_results.csv")


# Embedded HTML:

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OCR Receipt Reviewer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #0d0f12;
    --surface:  #13161b;
    --border:   #1e2530;
    --border2:  #2a3344;
    --text:     #c8d0dc;
    --muted:    #4a5568;
    --accent:   #3b82f6;
    --green:    #22c55e;
    --red:      #ef4444;
    --yellow:   #f59e0b;
    --dim:      #2d3748;
    --mono:     'IBM Plex Mono', monospace;
    --sans:     'IBM Plex Sans', sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--sans);
         font-size: 13px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  /* ── Header ── */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 20px; height: 50px; border-bottom: 1px solid var(--border);
    background: var(--surface); flex-shrink: 0;
  }
  .logo { font-family: var(--mono); font-size: 13px; font-weight: 600;
          letter-spacing: .12em; color: var(--accent); }
  .logo span { color: var(--muted); font-weight: 400; }
  .header-actions { display: flex; gap: 10px; align-items: center; }

  /* ── Buttons ── */
  button {
    font-family: var(--mono); font-size: 11px; font-weight: 500;
    letter-spacing: .06em; cursor: pointer; border: none; border-radius: 4px;
    padding: 7px 14px; transition: all .15s;
  }
  .btn-primary   { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: #2563eb; }
  .btn-ghost  { background: transparent; color: var(--text); border: 1px solid var(--border2); }
  .btn-ghost:hover  { border-color: var(--accent); color: var(--accent); }
  .btn-green  { background: #16532a; color: var(--green); border: 1px solid #22c55e44; }
  .btn-green:hover  { background: #166534; }
  .btn-sm { padding: 4px 10px; font-size: 10px; }
  button:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Layout ── */
  .workspace {
    display: flex; flex: 1; overflow: hidden;
  }

  /* ── Sidebar ── */
  .sidebar {
    width: 220px; flex-shrink: 0; border-right: 1px solid var(--border);
    display: flex; flex-direction: column; background: var(--surface);
  }
  .sidebar-header {
    padding: 12px 14px; border-bottom: 1px solid var(--border);
    font-family: var(--mono); font-size: 10px; letter-spacing: .1em;
    color: var(--muted); text-transform: uppercase;
    display: flex; justify-content: space-between; align-items: center;
  }
  .file-count { background: var(--dim); color: var(--accent);
                border-radius: 10px; padding: 1px 7px; font-size: 10px; }
  .file-list { flex: 1; overflow-y: auto; }
  .file-item {
    padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 3px; transition: background .1s;
  }
  .file-item:hover    { background: var(--dim); }
  .file-item.active   { background: #1a2540; border-left: 2px solid var(--accent); }
  .file-item .fname   { font-family: var(--mono); font-size: 11px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .file-item .fmeta   { display: flex; gap: 6px; align-items: center; }
  .acc-badge {
    font-family: var(--mono); font-size: 10px; font-weight: 600; padding: 1px 6px;
    border-radius: 3px;
  }
  .acc-high   { background: #14532d44; color: var(--green); border: 1px solid #22c55e33; }
  .acc-mid    { background: #78350f44; color: var(--yellow); border: 1px solid #f59e0b33; }
  .acc-low    { background: #7f1d1d44; color: var(--red);    border: 1px solid #ef444433; }

  /* ── Folder picker ── */
  .folder-bar {
    padding: 10px 14px; border-top: 1px solid var(--border); flex-shrink: 0;
  }
  .folder-bar input {
    width: 100%; background: var(--bg); border: 1px solid var(--border2);
    color: var(--text); padding: 6px 8px; border-radius: 4px;
    font-family: var(--mono); font-size: 10px; margin-bottom: 6px;
  }
  .folder-bar input:focus { outline: none; border-color: var(--accent); }

  /* ── Main viewer ── */
  .viewer {
    flex: 1; display: flex; overflow: hidden;
  }
  .image-panel {
    flex: 0 0 42%; border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .panel-header {
    padding: 10px 16px; border-bottom: 1px solid var(--border);
    font-family: var(--mono); font-size: 10px; letter-spacing: .1em;
    color: var(--muted); text-transform: uppercase; flex-shrink: 0;
    display: flex; justify-content: space-between; align-items: center;
  }
  .image-wrap {
    flex: 1; overflow: auto; display: flex; align-items: flex-start;
    justify-content: center; padding: 16px; background: #090b0e;
  }
  .image-wrap img { max-width: 100%; border-radius: 4px;
                    box-shadow: 0 8px 40px #00000080; display: block; }

  /* ── Table panel ── */
  .table-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .table-scroll { flex: 1; overflow-y: auto; }

  /* ── Accuracy bar ── */
  .accuracy-bar {
    padding: 10px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px; flex-shrink: 0;
  }
  .acc-label { font-family: var(--mono); font-size: 10px; color: var(--muted); text-transform: uppercase; }
  .acc-value { font-family: var(--mono); font-size: 18px; font-weight: 600; }
  .progress-track {
    flex: 1; height: 4px; background: var(--dim); border-radius: 2px; overflow: hidden;
  }
  .progress-fill {
    height: 100%; border-radius: 2px; transition: width .4s, background .4s;
  }
  .conf-label { font-family: var(--mono); font-size: 10px; color: var(--muted); }

  /* ── Field table ── */
  table { width: 100%; border-collapse: collapse; }
  th {
    padding: 9px 14px; text-align: left; font-family: var(--mono);
    font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
    color: var(--muted); border-bottom: 1px solid var(--border);
    background: var(--surface); position: sticky; top: 0; z-index: 2;
  }
  td { padding: 9px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:hover td { background: #0f1318; }
  .field-label { font-family: var(--mono); font-size: 11px; color: #8b9bb4; }
  .field-value {
    font-family: var(--mono); font-size: 11px; color: var(--text);
    max-width: 160px; word-break: break-all;
  }
  .status-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-family: var(--mono); font-size: 10px; font-weight: 600;
    padding: 2px 8px; border-radius: 3px; letter-spacing: .04em; white-space: nowrap;
  }
  .s-CORRECT    { background: #14532d44; color: var(--green); border: 1px solid #22c55e33; }
  .s-MISMATCH   { background: #7f1d1d44; color: var(--red);   border: 1px solid #ef444433; }
  .s-SUSPECT    { background: #78350f44; color: var(--yellow); border: 1px solid #f59e0b33; }
  .s-NOT_FOUND  { background: #1e293b;   color: var(--muted); border: 1px solid #2a3344; }
  .s-MISSING    { background: #7f1d1d44; color: var(--red);   border: 1px solid #ef444433; }

  /* ── Inline edit ── */
  .edit-wrap { display: flex; gap: 6px; align-items: center; }
  .edit-input {
    background: var(--bg); border: 1px solid var(--border2); color: var(--text);
    padding: 4px 8px; border-radius: 3px; font-family: var(--mono);
    font-size: 11px; width: 140px;
  }
  .edit-input:focus { outline: none; border-color: var(--accent); }

  /* ── Empty state ── */
  .empty-state {
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 12px; color: var(--muted);
  }
  .empty-icon { font-size: 36px; opacity: .3; }
  .empty-text { font-family: var(--mono); font-size: 12px; letter-spacing: .05em; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--dim); border-radius: 3px; }

  /* ── Toast ── */
  #toast {
    position: fixed; bottom: 20px; right: 20px; padding: 10px 18px;
    background: var(--surface); border: 1px solid var(--border2);
    border-radius: 6px; font-family: var(--mono); font-size: 11px;
    color: var(--text); opacity: 0; transition: opacity .25s; pointer-events: none;
    box-shadow: 0 4px 20px #00000060; z-index: 100;
  }
  #toast.show { opacity: 1; }
  #toast.ok   { border-color: var(--green); color: var(--green); }
  #toast.err  { border-color: var(--red);   color: var(--red); }
</style>
</head>
<body>

<header>
  <div class="logo">OCR<span>/</span>REVIEWER <span style="font-size:10px;margin-left:8px;">RECEIPT BENCHMARK TOOL</span></div>
  <div class="header-actions">
    <span id="nav-info" style="font-family:var(--mono);font-size:10px;color:var(--muted)"></span>
    <button class="btn-ghost btn-sm" id="btn-prev" onclick="navigate(-1)" disabled>◀ PREV</button>
    <button class="btn-ghost btn-sm" id="btn-next" onclick="navigate(1)"  disabled>NEXT ▶</button>
    <button class="btn-green btn-sm" id="btn-export" onclick="exportCSV()" disabled>⬇ EXPORT CSV</button>
  </div>
</header>

<div class="workspace">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      Files <span id="file-count" class="file-count">0</span>
    </div>
    <div class="file-list" id="file-list"></div>
    <div class="folder-bar">
      <input type="text" id="folder-input" placeholder="/path/to/receipts folder" />
      <button class="btn-primary" style="width:100%" onclick="loadFolder()">LOAD FOLDER</button>
    </div>
  </aside>

  <!-- Viewer -->
  <div class="viewer" id="viewer">
    <!-- Image panel -->
    <div class="image-panel">
      <div class="panel-header">
        <span>RECEIPT IMAGE</span>
        <span id="img-filename" style="color:var(--text);font-size:10px"></span>
      </div>
      <div class="image-wrap" id="image-wrap">
        <div class="empty-state">
          <div class="empty-icon">🧾</div>
          <div class="empty-text">SELECT A FILE TO BEGIN</div>
        </div>
      </div>
    </div>

    <!-- Table panel -->
    <div class="table-panel">
      <div class="panel-header" style="border-bottom:none">
        <span>PREDICTED FIELDS</span>
        <span id="table-subtitle" style="color:var(--muted)"></span>
      </div>
      <div class="accuracy-bar" id="accuracy-bar" style="display:none">
        <span class="acc-label">Accuracy</span>
        <span class="acc-value" id="acc-value">—</span>
        <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
        <span class="conf-label" id="conf-label"></span>
      </div>
      <div class="table-scroll" id="table-scroll">
        <div class="empty-state" id="table-empty">
          <div class="empty-icon" style="font-size:24px">📋</div>
          <div class="empty-text">NO DATA LOADED</div>
        </div>
        <table id="data-table" style="display:none">
          <thead>
            <tr>
              <th style="width:28%">Field</th>
              <th style="width:30%">Predicted Value</th>
              <th style="width:20%">Status</th>
              <th style="width:22%">Action</th>
            </tr>
          </thead>
          <tbody id="table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
let files    = [];
let current  = -1;
let stemData = {};

// ── Folder load ───────────────────────────────────────────────────────────────
async function loadFolder() {
  const folder = document.getElementById('folder-input').value.trim();
  if (!folder) { toast('Enter a folder path', 'err'); return; }
  const res  = await fetch('/api/folder', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({folder})
  });
  const data = await res.json();
  if (data.error) { toast(data.error, 'err'); return; }
  files = data.pairs;
  document.getElementById('file-count').textContent = files.length;
  renderSidebar();
  document.getElementById('btn-export').disabled = files.length === 0;
  document.getElementById('btn-prev').disabled   = true;
  document.getElementById('btn-next').disabled   = files.length <= 1;
  if (files.length) loadFile(0);
  toast(`Loaded ${files.length} file(s)`, 'ok');
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function renderSidebar() {
  const list = document.getElementById('file-list');
  list.innerHTML = files.map((f, i) => {
    const d   = stemData[f.id];
    const acc = d ? d.accuracy : null;
    const cls = acc === null ? '' : acc >= 80 ? 'acc-high' : acc >= 60 ? 'acc-mid' : 'acc-low';
    const badge = acc !== null
      ? `<span class="acc-badge ${cls}">${acc}%</span>` : '';
    return `<div class="file-item ${i===current?'active':''}" onclick="loadFile(${i})">
      <span class="fname">${f.id}</span>
      <div class="fmeta">${badge}</div>
    </div>`;
  }).join('');
}

// ── File load ─────────────────────────────────────────────────────────────────
async function loadFile(idx) {
  current = idx;
  const f   = files[idx];
  const res = await fetch(`/api/load/${f.id}`);
  const d   = await res.json();
  if (d.error) { toast(d.error, 'err'); return; }
  stemData[f.id] = d;

  // Nav
  document.getElementById('btn-prev').disabled = idx === 0;
  document.getElementById('btn-next').disabled = idx === files.length - 1;
  document.getElementById('nav-info').textContent = `${idx+1} / ${files.length}`;

  // Image
  document.getElementById('image-wrap').innerHTML =
    `<img src="${d.image}" alt="Receipt" />`;
  document.getElementById('img-filename').textContent = f.id;

  // Table
  renderTable(d);
  renderSidebar();
}

function navigate(dir) {
  const next = current + dir;
  if (next >= 0 && next < files.length) loadFile(next);
}

// ── Table render ──────────────────────────────────────────────────────────────
function renderTable(d) {
  document.getElementById('table-empty').style.display = 'none';
  document.getElementById('data-table').style.display  = 'table';
  document.getElementById('accuracy-bar').style.display = 'flex';

  updateAccuracy(d.accuracy, d.conf);
  document.getElementById('table-subtitle').textContent = d.fileName.split(/[\\/]/).pop();

  const body = document.getElementById('table-body');
  body.innerHTML = d.rows.map(r => {
    const val = Array.isArray(r.value) ? r.value.join(', ') : (r.value ?? '—');
    const statusCls = `s-${r.status}`;
    const icon = {CORRECT:'✔', MISMATCH:'✘', SUSPECT:'?', NOT_FOUND:'–', MISSING:'✘'}[r.status] || '?';
    return `<tr id="row-${r.key}">
      <td><span class="field-label">${r.label}</span></td>
      <td><span class="field-value" id="val-${r.key}">${val}</span></td>
      <td>
        <span class="status-chip ${statusCls}" id="chip-${r.key}" title="${r.note}">
          ${icon} ${r.status.replace('_',' ')}
        </span>
      </td>
      <td>
        <div class="edit-wrap">
          <input class="edit-input" id="input-${r.key}" value="${val === '—' ? '' : val}"
                 placeholder="edit…" onkeydown="if(event.key==='Enter')saveField('${d.stem||files[current].id}','${r.key}')"/>
          <button class="btn-ghost btn-sm" onclick="saveField('${d.stem||files[current].id}','${r.key}')">✓</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function updateAccuracy(acc, conf) {
  const el   = document.getElementById('acc-value');
  const fill = document.getElementById('progress-fill');
  el.textContent   = acc + '%';
  el.style.color   = acc >= 80 ? 'var(--green)' : acc >= 60 ? 'var(--yellow)' : 'var(--red)';
  fill.style.width = acc + '%';
  fill.style.background = acc >= 80 ? 'var(--green)' : acc >= 60 ? 'var(--yellow)' : 'var(--red)';
  document.getElementById('conf-label').textContent = `Model conf: ${conf}%`;
}

// ── Save field ────────────────────────────────────────────────────────────────
async function saveField(stem, key) {
  const val = document.getElementById(`input-${key}`).value.trim();
  const res = await fetch(`/api/save/${stem}`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key, value: val})
  });
  const d = await res.json();
  if (d.error) { toast(d.error, 'err'); return; }

  // Update in-memory and re-render row
  const row = d.rows.find(r => r.key === key);
  if (row) {
    const icon     = {CORRECT:'✔',MISMATCH:'✘',SUSPECT:'?',NOT_FOUND:'–',MISSING:'✘'}[row.status]||'?';
    const chip     = document.getElementById(`chip-${key}`);
    const valSpan  = document.getElementById(`val-${key}`);
    chip.className = `status-chip s-${row.status}`;
    chip.textContent= `${icon} ${row.status.replace('_',' ')}`;
    chip.title      = row.note;
    valSpan.textContent = val || '—';
  }
  stemData[files[current].id].accuracy = d.accuracy;
  stemData[files[current].id].rows     = d.rows;
  updateAccuracy(d.accuracy, stemData[files[current].id].conf);
  renderSidebar();
  toast(`Saved — accuracy now ${d.accuracy}%`, 'ok');
}

// ── Export CSV ────────────────────────────────────────────────────────────────
function exportCSV() {
  window.location.href = '/api/export';
  toast('Downloading CSV…', 'ok');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let toastTimer;
function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className   = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = '', 2800);
}

// Auto-load if folder was passed via CLI
window.addEventListener('DOMContentLoaded', () => {
  fetch('/api/files').then(r => r.json()).then(d => {
    if (d.pairs && d.pairs.length) {
      files = d.pairs;
      document.getElementById('file-count').textContent = files.length;
      document.getElementById('btn-export').disabled = false;
      document.getElementById('btn-next').disabled   = files.length <= 1;
      renderSidebar();
      loadFile(0);
    }
  });
});
</script>
</body>
</html>"""


# Entry point:

if __name__ == "__main__":
    import webbrowser, threading
    port = 5050

    if TARGET_FOLDER and not TARGET_FOLDER.is_dir():
        print(f"⚠  Folder not found: {TARGET_FOLDER}")
        TARGET_FOLDER = None

    print(f"\n  OCR Receipt Reviewer")
    print(f"  ──────────────────────────────")
    if TARGET_FOLDER:
        print(f"  Folder : {TARGET_FOLDER}")
        pairs = scan_folder(TARGET_FOLDER)
        print(f"  Files  : {len(pairs)} receipt pair(s) found")
    print(f"  URL    : http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")

    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(port=port, debug=False)