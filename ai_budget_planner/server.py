"""FastAPI backend: the agents, models and optimiser as a JSON API, plus the HTML/CSS/JS frontend.
Run:  uvicorn server:app --reload     then open http://127.0.0.1:8000"""
import io, secrets
from datetime import date
from typing import Optional, Union
import numpy as np, pandas as pd
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import core, agents, reasoning, importer, payment_ocr
from core import CATS, q


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://YOUR-VERCEL-DOMAIN.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

core.init_db()
q("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER)")

def clean(o):
    """Make anything JSON-safe (numpy numbers, NaN, DataFrames)."""
    if isinstance(o, pd.DataFrame): o = o.to_dict("records")
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, np.generic): o = o.item()
    return None if isinstance(o, float) and (o != o or abs(o) == float("inf")) else o

def uid_of(authorization: Optional[str] = Header(None)):
    token = (authorization or "").removeprefix("Bearer ").strip()

    r = q(
        "SELECT user_id FROM sessions WHERE token=?",
        token
    )

    if not r:
        raise HTTPException(401, "Please log in.")

    return r[0][0]
def session(uid):
    tok = secrets.token_urlsafe(32); q("INSERT INTO sessions VALUES(?,?)", tok, uid); return dict(token=tok, **me(uid))
def me(uid=Depends(uid_of)): return dict(user=q("SELECT username FROM users WHERE id=?", uid)[0][0], cats=CATS, groups=core.GROUPS)
app.get("/api/me")(me)

class Cred(BaseModel): username: str; password: str
class PlanIn(BaseModel): income: float; goal: float; budgets: dict[str, float]
class TextIn(BaseModel): text: str; date: Optional[str] = None
class ConfirmIn(BaseModel):
    amount: float; description: str; date: str; kind: str = "expense"; category: Optional[str] = None; source: str = "screenshot"
    learn_lead: Union[str, bool, None] = None
class RowsIn(BaseModel): rows: list[dict]

# ---------- accounts ----------
@app.post("/api/register")
def register(b: Cred):
    uid, msg = core.register(b.username, b.password)
    if not uid: raise HTTPException(400, msg)
    return session(uid)
@app.post("/api/login")
def login(b: Cred):
    uid = core.login(b.username, b.password)
    if not uid: raise HTTPException(400, "Wrong username or password.")
    return session(uid)
@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    q("DELETE FROM sessions WHERE token=?", token)

# ---------- plans and months ----------
@app.get("/api/plan/{ym}")
def get_plan(ym: str, uid: int = Depends(uid_of)):
    pl, prev = core.get_plan(uid, ym), core.last_plan_ym(uid, ym); pp = pl or (core.get_plan(uid, prev) if prev else None)
    default = (40000, 8000, dict(Food=7000, Transport=3000, Rent=15000, Bills=2500, Shopping=4000, Entertainment=2500, Health=1000, Education=500, Other=1000))
    income, goal, budgets = pp or default
    return dict(exists=bool(pl), income=income, goal=goal, budgets=budgets, prefilled_from=None if pl or not pp else prev)
@app.put("/api/plan/{ym}")
def put_plan(ym: str, b: PlanIn, uid: int = Depends(uid_of)):
    if sum(b.budgets.values()) + b.goal > b.income: raise HTTPException(400, "Budgets plus savings are more than income.")
    core.save_plan(uid, ym, b.income, b.goal, b.budgets)
@app.post("/api/plan/{ym}/replan")
def replan(ym: str, uid: int = Depends(uid_of)): return clean(core.replan(uid, ym, "manual re-plan"))
@app.post("/api/plan/{ym}/undo")
def undo(ym: str, uid: int = Depends(uid_of)): return core.undo_change(uid, ym)
@app.get("/api/plan/{ym}/explain")
def explain(ym: str, uid: int = Depends(uid_of)):
    ch = core.last_change(uid, ym); return reasoning.explain(ch["reasoning"]) if ch else dict(text="", source="")
@app.get("/api/month/{ym}")
def month(ym: str, uid: int = Depends(uid_of)):
    tx = core.txns(uid, None); flag, _ = core.anomaly_mask(tx[tx.kind.fillna("expense") == "expense"]); tx["unusual"] = tx.index.map(lambda i: bool(flag.get(i, False)))
    mp = core.month_plan(uid, ym); out = dict(ym=ym, has_plan=bool(mp), upcoming=ym >= date.today().isoformat()[:7], change=core.last_change(uid, ym),
                                              transactions=tx[tx.date.str.startswith(ym)].sort_values("date", ascending=False).drop(columns=["user_id"]))
    if mp:
        out.update({k: mp[k] for k in ("rows", "needs_replan", "income", "base_income", "received", "goal", "budgets", "spent")})
    return clean(out)
@app.get("/api/summary")
def summary(uid: int = Depends(uid_of)): return clean(core.month_summary(uid))

# ---------- adding money paid / received ----------
def shape(res):
    t, st = res.get("txn", {}), res.get("status") or {}
    return clean(dict(txn=t, ym=(t.get("date") or "")[:7], need_confirm=bool(res.get("need_confirm")), income_added=bool(res.get("income_added")), alerts=st.get("alerts", []),
                      no_plan=bool(st.get("no_plan")), change=res.get("change"), log=res.get("log", []),
                      error=None if t.get("amount") else "I couldn't find an amount. Try something like 'spent 200 on tea'."))
def safe(fn, *a, **k):
    try: return fn(*a, **k)
    except FileNotFoundError as e: raise HTTPException(503, str(e))     # models not trained yet

@app.post("/api/expense/text")
def expense_text(b: TextIn, uid: int = Depends(uid_of)): return shape(safe(agents.run, uid, text=b.text, on_date=b.date))
@app.post("/api/expense/screenshot")
async def screenshot(file: UploadFile = File(...), uid: int = Depends(uid_of)):
    r = payment_ocr.extract(await file.read())
    if r.get("kind") != "income": r["suggested"], r["confidence"], r["suggest_source"] = safe(core.predict_category_ex, r["description"], uid)
    return clean(r)
@app.post("/api/expense/confirm")
def confirm(b: ConfirmIn, uid: int = Depends(uid_of)):
    if b.learn_lead is not None: payment_ocr.learn_lead(b.learn_lead)
    t = dict(amount=b.amount, description=b.description, date=b.date, source=b.source)
    if b.kind == "income": return shape(agents.add_income(uid, t))
    if b.category not in CATS: raise HTTPException(400, "Please choose a category.")
    return shape(safe(agents.confirm, uid, t, b.category))
@app.delete("/api/txn/{tid}")
def delete(tid: int, uid: int = Depends(uid_of)): core.delete_txn(uid, tid)

# ---------- import ----------
@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...), uid: int = Depends(uid_of)):
    f = io.BytesIO(await file.read()); f.name = file.filename or "file"; text = ""
    try: df = importer.parse(f)
    except Exception as e: raise HTTPException(400, str(e))
    pdf = f.name.lower().endswith(".pdf")
    if df.empty and pdf: text = importer.read_pdf_text(f)
    if not df.empty:
        df = df.assign(category=[c if c in CATS else core.predict_category(x, uid)[0] for c, x in zip(df.get("category", [None] * len(df)), df.description)], kind=df.get("kind", "expense"))
    return clean(dict(rows=df, text=text[:3000], no_text=pdf and df.empty and not text.strip()))
@app.post("/api/import/commit")
def import_commit(b: RowsIn, uid: int = Depends(uid_of)):
    core.import_df(uid, pd.DataFrame(b.rows)[["date", "description", "amount", "category", "kind"]]); return dict(count=len(b.rows))

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")