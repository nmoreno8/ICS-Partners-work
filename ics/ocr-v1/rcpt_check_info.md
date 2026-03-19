# Receipt Benchmark Evaluation Checker
Created by: Noah Moreno

### Summary
The script is a receipt OCR accuracy checker and corrector. You give it the JSON output file from the OCR model, and it does four main things:

1. Automatically grades the model's predictions. It reads the `predict` block and checks each field (title, date, total, tax, phone, etc.) against the raw receipt text in `all_texts`. Each field gets a status: ✔ Correct, ✘ Mismatch, ? Suspicious, or – Confirmed absent. It then gives you an overall accuracy percentage.

2. Catches hidden mistakes. If the model returns `NOT_FOUND` for a field, the script double-checks the raw receipt text using pattern matching. If the data actually is there, it flags it as `SUSPECT` so you know the model likely missed it.

3. Lets you manually correct any field. If a prediction is wrong, you can pick the field by number, type the correct value, and the accuracy score recalculates immediately. This is the "quick adapt" part — you correct once, benchmark updates instantly.

4. Saves your corrections. Once you're done correcting, it writes a new `_corrected.json` file alongside the original, preserving the original data untouched.

In short,it loads a receipt JSON → see what the model got right or wrong → fix mistakes interactively → save the corrected version.

### How Program works (in greater detail)
1. `evaluate_field()` — for each predict key, it strips whitespace/¥ symbols and does an exact substring search in `all_texts`. If the value isn't found, it runs a fuzzy similarity score (via `SequenceMatcher`) to catch format mismatches vs genuine errors.

2. `NOT_FOUND` detection — it re-checks `all_texts` with a regex hint pattern per field. If the pattern matches, the `NOT_FOUND` is flagged as `SUSPECT` (model may have missed it).

3. Text blocks - option `[1]` lets you view the raw information pulled from the JSON `text_blocks`.

4. Interactive loop — option `[2]` lets you pick any field by number and type a corrected value. The benchmark re-runs instantly after each correction session.

5. Difference view — option `[3]` shows exactly what changed from original to your corrections.

6. Save — option `[4]` writes the corrected data to `*_corrected.json` without overwriting the original.

7. Re-run — option `[5]` reloads the benchmark without editing

## Example (using `P0HPJH_output.json`)

Here is the example input I used to run the program in my terminal (python and json file must be in same directory/folder):
`python3 rcpt_bench_check.py P0HPJH_output.json`

And the output:
![Image](screenshots/sc_benchcheck_0.png)

### With those six options, here are the results of all of them:

When typing 1 and entering, you get the JSON raw text block input from the file being used
![Image](screenshots/sc_benchcheck_1.png)

When typing 2 and entering, you are able to edit the field numbers to correct what is wrong
![Image](screenshots/sc_benchcheck_2.png)

When typing 3 and entering, you are able to see the original file versus the corrected file
![Image](screenshots/sc_benchcheck_3.png)

When typing 4 and entering, the corrected JSON file is saved to your directory/computer
![Image](screenshots/sc_benchcheck_4.png)

When typing 5 and entering, the benchmark checker is re-ran with updated accuracy
![Image](screenshots/sc_benchcheck_5.png)