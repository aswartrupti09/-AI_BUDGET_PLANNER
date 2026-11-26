# AI Budget Planner

Trained models do the predicting, a linear-programming solver does the budget maths, LangGraph agents run the workflow,
OCR reads payment screenshots, and an LLM (optional) only explains the changed budget in words.

## Run
```bash
pip install -r requirements.txt
python train.py                      # trains the models (needed once; makes its own synthetic training data)
uvicorn server:app --reload          # then open http://127.0.0.1:8000
```
Optional: `export ANTHROPIC_API_KEY=...` makes Claude write the explanation of a changed budget (any figure it adds that is not in the computed facts is rejected) and enables a vision fallback for screenshots. Without a key the app shows a built-in explanation.

## Files
| File | Job |
|---|---|
| `server.py` | FastAPI backend: login, plans, expenses, screenshots, explanations, import. Serves `frontend/` |
| `frontend/` | The website: `index.html`, `styles.css`, `app.js` (no outside libraries; works offline, light and dark theme) |
| `core.py` | Database, models, anomaly cleaning, category rules (fixed / necessity / optional), budget optimiser, plan-change log with undo |
| `agents.py` | LangGraph pipeline with four agents: capture, record, monitor, planner (the planner changes the budgets when spending breaks them) |
| `reasoning.py` | Why a budget changed, by category type (rent fixed, food a necessity, shopping optional); AI explanation (Claude if a key is set, numbers verified; else built-in) |
| `payment_ocr.py` | Screenshot to amount, merchant, date, paid/received (RapidOCR + image check for the rupee sign) |
| `importer.py` | CSV / PDF statements to rows (previewed before saving); also reads real training datasets |
| `train.py` | Trains and evaluates all models; writes `models/` |
| `data_gen.py` | Synthetic training data. Used by `train.py` only, never shown in the app |

## Models (all trained in `train.py`)
Categorizer (TF-IDF + logistic regression), forecaster (random forest), overspend-risk (boosted trees), anomaly detector (isolation forest).
Metrics are in `models/metrics.json` and printed by `train.py`. The forecaster feeds the planner's month-end estimates (not shown as a chart).

## Using a real dataset
Put any expense CSV at `data/real.csv` and run `python train.py`. Columns (date, description, amount, category, debit/credit, user) are found by name,
and the dataset's own category labels are mapped onto ours by `importer.py` (edit `LABELS` there to add words). Training then also reports accuracy on
held-out real merchants. Delete `data/real.csv` to go back to synthetic-only training.

## Deploying on Render
* Build command: `pip install -r requirements.txt && python train.py`   Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT` (one worker only; each extra worker repeats the memory use).
* Set the environment variable `PYTHON_VERSION` to a 3.12 release. Do **not** install `easyocr`: it loads PyTorch (about 1 GB of RAM) and will crash a 512 MB instance.
* Memory with screenshot reading (RapidOCR) was about 320 MB at its peak in testing. If the instance is still too small, set `OCR_ENGINE=vision` and `ANTHROPIC_API_KEY` to read screenshots with Claude instead (no local OCR memory).
* The free plan sleeps after about 15 minutes idle, so the first request after a pause is slow.
* SQLite lives on Render's temporary disk and is erased on every deploy or restart. For real use, attach a persistent disk or move to Postgres.