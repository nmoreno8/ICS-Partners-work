"""
Receipt OCR Accuracy Checker & Corrector:
Loads a receipt OCR JSON file, evaluates predicted fields against the raw
text, lets the user add corrections, and re-benchmarks accuracy on the go.
"""


import json
import re
import sys
import copy
from pathlib import Path
from difflib import SequenceMatcher


# Color helpers:
# (may or may not include in final version)

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# Field definitions: 
# Maps each predict key to be more human-readable with labels as well as search hints used for
# auto-validation against all_texts / text_blocks (turned into metadata).

FIELD_META = {
    "title":          {"label": "Title / Document Type",   "hint": r"領収"},
    "corporate_id":   {"label": "Corporate/Tax ID",        "hint": r"T?\d{10,13}"},
    "company":        {"label": "Company Name",            "hint": r"株式会社|イオン"},
    "total":          {"label": "Total Amount (¥)",        "hint": r"合計"},
    "subtotal":       {"label": "Sub-Total (¥)",           "hint": r"小計"},
    "deposit":        {"label": "Deposit / Cash Paid (¥)", "hint": r"預り|お預"},
    "eight_pct_tax":  {"label": "8% Tax Base Amount (¥)",  "hint": r"外税8"},
    "ten_pct_tax":    {"label": "10% Tax Base Amount (¥)", "hint": r"外税10|10%"},
    "change_amnt":    {"label": "Change (¥)",              "hint": r"お釣り"},
    "date":           {"label": "Date",                    "hint": r"\d{4}[年/]\d{1,2}[月/]\d{1,2}"},
    "time":           {"label": "Time",                    "hint": r"\d{1,2}:\d{2}"},
    "zip":            {"label": "Zip / Postal Code",       "hint": r"\d{3}-\d{4}"},
    "tel":            {"label": "Telephone",               "hint": r"\d{2,4}-\d{3,4}-\d{3,4}"},
}

NOT_FOUND_MARKERS = {"NOT_FOUND", "NOT_FOUND_ZIP"}


# Json I/O helpers:

def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{GREEN}✔ Saved → {path}{RESET}")


# Evalution helpers:

def fuzzy_score(a: str, b: str) -> float:
    "0-1 similarity ratio between two strings (to become normalized)"
    a = re.sub(r"[\s¥,￥　]", "", str(a).lower())
    b = re.sub(r"[\s¥,￥　]", "", str(b).lower())
    return SequenceMatcher(None, a, b).ratio()

def value_in_text(value: str, all_texts: str) -> bool:
    "Return True if the cleaned predicted value appears somewhere in all_texts."
    if not value or value in NOT_FOUND_MARKERS:
        return False
    clean_val = re.sub(r"[\s¥,￥　]", "", value)
    clean_txt = re.sub(r"[\s¥,￥　]", "", all_texts)
    return clean_val in clean_txt

def normalize_list(v) -> list:
    "Wrap scalar predict values in a list for uniform handling."
    if isinstance(v, list):
        return v
    return [v]


# Scoring logic:

def evaluate_field(key: str, raw_value, all_texts: str) -> dict:
    """
    Returns a dict:
      status  : "CORRECT" | "NOT_FOUND_OK" | "NOT_FOUND_SUSPECT" | "MISMATCH"
      score   : 0.0 – 1.0
      note    : short explanation of the evaluation result
    """
    values = normalize_list(raw_value)
    all_not_found = all(v in NOT_FOUND_MARKERS for v in values)

    if all_not_found:
        # Check if the hint pattern actually appears -> suspect
        hint = FIELD_META.get(key, {}).get("hint", "")
        if hint and re.search(hint, all_texts):
            return {"status": "NOT_FOUND_SUSPECT", "score": 0.0,
                    "note": "Model returned NOT_FOUND but pattern found in receipt"}
        return {"status": "NOT_FOUND_OK", "score": 1.0,
                "note": "Not present on receipt (confirmed)"}

    # At least one real value – check each against all_texts
    found_any = False
    best_score = 0.0
    for v in values:
        if v in NOT_FOUND_MARKERS:
            continue
        if value_in_text(v, all_texts):
            found_any = True
            best_score = 1.0
            break
        # Partial/fuzzy check to catch formatting differences
        s = fuzzy_score(v, all_texts)
        best_score = max(best_score, s)

    if found_any:
        return {"status": "CORRECT", "score": 1.0, "note": "Value is in receipt text"}
    if best_score >= 0.6:
        return {"status": "MISMATCH", "score": best_score,
                "note": f"Partial match ({best_score:.0%}) – may be formatting difference"}
    return {"status": "MISMATCH", "score": best_score,
            "note": "Value NOT found in receipt text"}

def benchmark(predict: dict, all_texts: str) -> tuple[dict, float]:
    "Run full evaluation for accuracy. Returns (results_dict, overall_accuracy_pct)."
    results = {}
    total_score = 0.0
    count = 0

    for key in FIELD_META:
        raw = predict.get(key) 
        if raw is None:
            results[key] = {"status": "MISSING", "score": 0.0,
                            "note": "Key absent from predict block",
                            "value": "—"}
        else:
            ev = evaluate_field(key, raw, all_texts)
            ev["value"] = raw
            results[key] = ev

        total_score += results[key]["score"]
        count += 1

    accuracy = (total_score / count * 100) if count else 0.0
    return results, accuracy


# Display (with colors and icons):

STATUS_COLOUR = {
    "CORRECT":           GREEN,
    "NOT_FOUND_OK":      DIM,
    "NOT_FOUND_SUSPECT": YELLOW,
    "MISMATCH":          RED,
    "MISSING":           RED,
}

STATUS_ICON = {
    "CORRECT":           "✔",
    "NOT_FOUND_OK":      "–",
    "NOT_FOUND_SUSPECT": "?",
    "MISMATCH":          "✘",
    "MISSING":           "✘",
}


def print_results(results: dict, accuracy: float, conf: float):
    print(f"\n{'─'*65}")
    print(f"  {BOLD}RECEIPT OCR ACCURACY REPORT{RESET}")
    print(f"{'─'*65}")
    header = f"{'Field':<22} {'Predicted Value':<20} {'Status':<20} Note"
    print(f"{BOLD}{header}{RESET}")
    print(f"{'─'*65}")

    for key, ev in results.items():
        meta = FIELD_META.get(key, {})
        label = meta.get("label", key)
        val   = str(ev.get("value", ""))
        if isinstance(ev["value"], list):
            val = ", ".join(str(x) for x in ev["value"])
        val_disp = val[:19] if len(val) > 19 else val

        status = ev["status"]
        col    = STATUS_COLOUR.get(status, "")
        icon   = STATUS_ICON.get(status, "?")

        print(f"{label:<22} {val_disp:<20} {col}{icon} {status:<18}{RESET} {DIM}{ev['note']}{RESET}")

    print(f"{'─'*65}")
    accuracy_col = GREEN if accuracy >= 80 else YELLOW if accuracy >= 60 else RED
    print(f"  {BOLD}Field Accuracy :{RESET} {accuracy_col}{accuracy:.1f}%{RESET}")
    print(f"  {BOLD}Model Conf (avg):{RESET} {conf:.2f}%")
    print(f"{'─'*65}\n")


def print_raw_blocks(text_blocks: dict):
    print(f"\n{CYAN}{BOLD}── Raw Text Blocks ──────────────────────────────────────{RESET}")
    for idx, line in text_blocks.items():
        print(f"  [{idx:>2}] {line}")
    print()


# Correction workflow:

def prompt_correction(predict: dict) -> dict:
    """
    Interactive loop that allows user to correct any predict field.
    Returns the (possibly) modified predict dict.
    """
    updated = copy.deepcopy(predict)

    print(f"\n{BOLD}Available fields to correct:{RESET}")
    keys = list(FIELD_META.keys())
    for i, k in enumerate(keys):
        label = FIELD_META[k]["label"]
        cur   = updated.get(k, "NOT SET")
        print(f"  {i+1:>2}. {label:<28} current → {CYAN}{cur}{RESET}")

    print(f"\n{DIM}Enter field number(s) to correct (comma-separated), or ENTER to skip:{RESET}")
    raw = input("  > ").strip()
    if not raw:
        return updated

    for token in raw.split(","):
        token = token.strip()
        if not token.isdigit():
            continue
        idx = int(token) - 1
        if idx < 0 or idx >= len(keys):
            print(f"{YELLOW}  Invalid number: {token}{RESET}")
            continue

        key = keys[idx]
        label = FIELD_META[key]["label"]
        cur   = updated.get(key, "NOT SET")
        print(f"\n  {BOLD}{label}{RESET}")
        print(f"  Current value : {CYAN}{cur}{RESET}")
        print(f"  Enter corrected value (ENTER to keep, 'NOT_FOUND' to mark absent):")
        new_val = input("  > ").strip()
        if new_val == "":
            print(f"  {DIM}Kept unchanged.{RESET}")
            continue

        # If original was a list, keep it as a list
        if isinstance(cur, list):
            updated[key] = [new_val]
        else:
            updated[key] = new_val
        print(f"  {GREEN}✔ Updated: {key} → {new_val}{RESET}")

    return updated


# Main:

def main():
    # Load file
    if len(sys.argv) > 1:
        json_path = sys.argv[1]
    else:
        json_path = input("Path to JSON file: ").strip().strip('"')

    data        = load_json(json_path)
    predict     = data.get("predict", {})
    all_texts   = data.get("all_texts", "")
    text_blocks = data.get("text_blocks", {})
    conf        = predict.get("average_conf", 0.0)

    # Keep an original copy for difference/comparison later
    original_predict = copy.deepcopy(predict)

    print(f"\n{BOLD}File loaded:{RESET} {data.get('fileName', json_path)}")
    print(f"{BOLD}Has label  :{RESET} {data.get('hasLabel', False)}")

    # Initial benchmark assessment
    results, accuracy = benchmark(predict, all_texts)
    print_results(results, accuracy, conf)

    # Main interaction loop
    while True:
        print(f"{BOLD}Options:{RESET}")
        print("  [1] Show raw text blocks")
        print("  [2] Correct predicted values & re-benchmark")
        print("  [3] Show field-by-field diff (original vs corrected)")
        print("  [4] Save corrected JSON")
        print("  [5] Re-run benchmark (no edits)")
        print("  [q] Quit")
        choice = input("\n  > ").strip().lower()

        if choice == "1":
            print_raw_blocks(text_blocks)

        elif choice == "2":
            predict = prompt_correction(predict)
            results, accuracy = benchmark(predict, all_texts)
            print_results(results, accuracy, conf)

        elif choice == "3":
            print(f"\n{BOLD}{'─'*55}{RESET}")
            print(f"  {BOLD}DIFF: Original  →  Corrected{RESET}")
            print(f"{'─'*55}")
            any_diff = False
            for k in FIELD_META:
                orig = original_predict.get(k)
                curr = predict.get(k)
                if orig != curr:
                    any_diff = True
                    label = FIELD_META[k]["label"]
                    print(f"  {label}")
                    print(f"    {RED}Before:{RESET} {orig}")
                    print(f"    {GREEN}After :{RESET} {curr}")
            if not any_diff:
                print(f"  {DIM}No changes made yet.{RESET}")
            print()

        elif choice == "4":
            data["predict"] = predict
            out_path = Path(json_path).with_stem(Path(json_path).stem + "_corrected")
            save_json(data, str(out_path))

        elif choice == "5":
            results, accuracy = benchmark(predict, all_texts)
            print_results(results, accuracy, conf)

        elif choice == "q":
            print(f"\n{DIM}Goodbye.{RESET}\n")
            break
        else:
            print(f"{YELLOW}  Unknown option.{RESET}")


if __name__ == "__main__":
    main()