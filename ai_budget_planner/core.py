"""Shared logic: database, parsing, model access, forecasting helpers and the budget optimiser."""
import calendar, hashlib, hmac, json, re, secrets, sqlite3, warnings
from datetime import date, datetime
from pathlib import Path
import joblib, numpy as np, pandas as pd
from scipy.optimize import linprog
warnings.filterwarnings("ignore", message="X does not have valid feature names")

ROOT = Path(__file__).parent; MODELS = ROOT/"models"; DB = ROOT/"planner.db"
CATS = ["Food", "Transport", "Rent", "Bills", "Shopping", "Entertainment", "Health", "Education", "Other"]
GROUPS = {"Fixed cost": ["Rent", "Bills"], "Necessity": ["Food", "Health", "Transport", "Education"], "Optional": ["Shopping", "Entertainment", "Other"]}
FLOOR_PCT = {"Rent": 1.0, "Bills": 0.9, "Food": 0.7, "Health": 0.7, "Transport": 0.6, "Education": 0.5}   # share of its budget a category always keeps
FLEX = {"Rent": .02, "Bills": .1, "Food": .25, "Health": .2, "Transport": .3, "Education": .3, "Shopping": 1.0, "Entertainment": 1.0, "Other": .8}   # how easily a category is cut
SAV_PEN = 5.0     # cost per rupee of missing the savings goal (cutting flexible categories is cheaper)
UNUSUAL_X = 4     # a payment is "unusual" only if it is also at least this many times the usual amount

# ---------------- database ----------------
def _conn(): return sqlite3.connect(DB)
def q(sql, *a):
    with _conn() as c: return c.execute(sql, a).fetchall()
def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, salt TEXT, pw_hash TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS month_plan(user_id INTEGER, ym TEXT, income REAL, goal REAL, PRIMARY KEY(user_id, ym));
        CREATE TABLE IF NOT EXISTS month_budget(user_id INTEGER, ym TEXT, category TEXT, budget REAL, start_budget REAL, PRIMARY KEY(user_id, ym, category));
        CREATE TABLE IF NOT EXISTS txn(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, description TEXT, amount REAL, category TEXT, source TEXT, kind TEXT DEFAULT 'expense');
        CREATE TABLE IF NOT EXISTS plan_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ym TEXT, ts TEXT, before TEXT, after TEXT, reasoning TEXT, trigger TEXT);
        CREATE TABLE IF NOT EXISTS feedback(user_id INTEGER, description TEXT, category TEXT, ts TEXT);""")
        try: c.execute("ALTER TABLE txn ADD COLUMN kind TEXT DEFAULT 'expense'")      # database made by an older version
        except sqlite3.OperationalError: pass

# accounts: passwords are salted and hashed with PBKDF2, never stored as plain text
def _hash(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
def register(username, password):
    u = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.]{3,20}", u): return None, "Username must be 3-20 characters: letters, numbers, _ or ."
    if len(password) < 6: return None, "Password must be at least 6 characters."
    salt = secrets.token_hex(16)
    try:
        with _conn() as c: return c.execute("INSERT INTO users(username,salt,pw_hash,created) VALUES(?,?,?,?)", (u, salt, _hash(password, salt), date.today().isoformat())).lastrowid, "ok"
    except sqlite3.IntegrityError: return None, "That username is already taken."
def login(username, password):
    r = q("SELECT id,salt,pw_hash FROM users WHERE username=?", username.strip().lower())
    return r[0][0] if r and hmac.compare_digest(_hash(password, r[0][1]), r[0][2]) else None

# one plan per user per month
def save_plan(uid, ym, income, goal, budgets):
    with _conn() as c:
        c.execute("REPLACE INTO month_plan VALUES(?,?,?,?)", (uid, ym, income, goal)); c.execute("DELETE FROM month_budget WHERE user_id=? AND ym=?", (uid, ym))
        c.executemany("INSERT INTO month_budget VALUES(?,?,?,?,?)", [(uid, ym, k, v, v) for k, v in budgets.items()])    # start_budget never changes later
def update_budgets(uid, ym, budgets):
    with _conn() as c: c.executemany("UPDATE month_budget SET budget=? WHERE user_id=? AND ym=? AND category=?", [(v, uid, ym, k) for k, v in budgets.items()])
# a plan change is applied, logged with its reasons, and can be undone
def apply_change(uid, ym, before, plan, reasoning, trigger=""):
    q("INSERT INTO plan_log(user_id,ym,ts,before,after,reasoning,trigger) VALUES(?,?,?,?,?,?,?)", uid, ym, datetime.now().strftime("%d %b, %H:%M"),
      json.dumps(before), json.dumps(plan["budgets"]), json.dumps({**reasoning, "savings": plan["savings"], "shortfall": plan["shortfall"]}), trigger)
    update_budgets(uid, ym, plan["budgets"])
def last_change(uid, ym):
    r = q("SELECT id,ts,before,after,reasoning,trigger FROM plan_log WHERE user_id=? AND ym=? ORDER BY id DESC LIMIT 1", uid, ym)
    return dict(id=r[0][0], ym=ym, ts=r[0][1], before=json.loads(r[0][2]), after=json.loads(r[0][3]), reasoning=json.loads(r[0][4]), trigger=r[0][5]) if r else None
def undo_change(uid, ym):
    ch = last_change(uid, ym)
    if ch: update_budgets(uid, ym, ch["before"]); q("DELETE FROM plan_log WHERE id=?", ch["id"])
    return bool(ch)
def replan(uid, ym, trigger="re-plan"):
    """Apply the optimiser's plan for this month if it changes anything. Returns the logged change (budgets before/after + reasons) or None."""
    mp = month_plan(uid, ym)
    if not mp or not mp["plan"]["changes"]: return None
    import reasoning   # imported here because reasoning imports core
    apply_change(uid, ym, mp["budgets"], mp["plan"], {**reasoning.build_reasoning(mp["budgets"], mp["spent"], mp["proj"], mp["income"], mp["goal"], mp["plan"]), "goal": mp["goal"]}, trigger)
    return last_change(uid, ym)
def get_plan(uid, ym):
    p, b = q("SELECT income,goal FROM month_plan WHERE user_id=? AND ym=?", uid, ym), dict(q("SELECT category,budget FROM month_budget WHERE user_id=? AND ym=?", uid, ym))
    return (p[0][0], p[0][1], b) if p and b else None
def get_start_plan(uid, ym): return dict(q("SELECT category,start_budget FROM month_budget WHERE user_id=? AND ym=?", uid, ym))
def last_plan_ym(uid, before): return q("SELECT MAX(ym) FROM month_plan WHERE user_id=? AND ym<?", uid, before)[0][0]
def received_by_month(uid, ym): return q("SELECT COALESCE(SUM(amount),0) FROM txn WHERE user_id=? AND kind='income' AND date LIKE ?", uid, ym + "%")[0][0]
def get_plan_eff(uid, ym):
    """Plan whose income includes money received that month ('Received from ...' payments)."""
    pl = get_plan(uid, ym); return (pl[0] + received_by_month(uid, ym), pl[1], pl[2]) if pl else None

# expenses (kind='expense') and money received (kind='income')
def add_txn(uid, t):
    q("INSERT INTO txn(user_id,date,description,amount,category,source,kind) VALUES(?,?,?,?,?,?,?)",
      uid, t["date"], t["description"], t["amount"], t.get("category", "Income"), t.get("source", "manual"), t.get("kind", "expense"))
def import_df(uid, df, source="report"):
    df = df.assign(user_id=uid, source=source)
    if "kind" not in df: df["kind"] = "expense"
    df["category"] = df.category.where(df.kind != "income", "Income")
    with _conn() as c: df[["user_id", "date", "description", "amount", "category", "source", "kind"]].to_sql("txn", c, if_exists="append", index=False)
def delete_txn(uid, i): q("DELETE FROM txn WHERE id=? AND user_id=?", int(i), uid)
def add_feedback(uid, desc, cat): q("INSERT INTO feedback VALUES(?,?,?,?)", uid, desc, cat, date.today().isoformat())
def txns(uid, kind="expense"):
    """kind: 'expense' (default), 'income', or None for both."""
    with _conn() as c: return pd.read_sql("SELECT * FROM txn WHERE user_id=?" + (" AND COALESCE(kind,'expense')=?" if kind else ""), c, params=[uid] + ([kind] if kind else []))
def spent_by_cat(uid, ym):
    d = txns(uid); return d[d.date.str.startswith(ym)].groupby("category").amount.sum().to_dict()
def monthly_history(uid, before=None, clean=True):
    """Monthly totals per category. clean=True caps unusually large one-off payments at the usual amount so one spike
    (e.g. a 6x rent payment) can't distort forecasts. Budgets and 'spent' always use the true amounts."""
    d = txns(uid)
    if d.empty: return d
    if clean: m, ref = anomaly_mask(d); d.loc[m, "amount"] = ref[m]
    d["ym"] = d.date.str[:7]
    return (d[d.ym < before] if before else d).groupby(["ym", "category"], as_index=False).amount.sum()
def month_summary(uid):
    """One row per month: planned income, money received, spent, saved (= income + received - spent), goal met?"""
    d = txns(uid, None); d["kind"] = d.kind.fillna("expense")
    t = d.assign(ym=d.date.str[:7]).groupby(["ym", "kind"]).amount.sum().unstack(fill_value=0) if len(d) else pd.DataFrame()
    pl = {r[0]: r[1:] for r in q("SELECT ym,income,goal FROM month_plan WHERE user_id=?", uid)}; rows = []
    for ym in sorted(set(t.index) | set(pl)):
        inc, goal = pl.get(ym, (None, None)); s, got = (t.loc[ym].get("expense", 0), t.loc[ym].get("income", 0)) if ym in t.index else (0, 0)
        saved = None if inc is None else round(inc + got - s)
        rows.append({"Month": ym, "Income": inc, "Received": round(got), "Spent": round(s), "Saved": saved, "Goal": goal, "Goal met": None if saved is None else saved >= goal})
    return pd.DataFrame(rows)

# ---------------- typed input ----------------
INCOME_WORDS = re.compile(r"\b(received|receive|got|credited|salary|income|earned|refund|pocket money|allowance)\b", re.I)
def parse_cash_text(text):
    t = dict(amount=None, description=text, date=date.today().isoformat(), source="cash", kind="expense")
    m = re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)", text, re.I)
    if not m: return t
    inc = bool(INCOME_WORDS.search(text)) and not re.search(r"\b(paid|spent|bought)\b", text, re.I)
    drop = r"\b(spent|paid|gave|bought|rs|inr|rupees|on|for|at|to|in|cash|of|a|the" + ("|received|receive|got|credited|from" if inc else "") + r")\b|₹"
    desc = re.sub(r"\s+", " ", re.sub(drop, " ", (text[:m.start()] + " " + text[m.end():]).lower())).strip()
    return {**t, "amount": float(m[1].replace(",", "")), "kind": "income" if inc else "expense", "description": desc or ("income" if inc else "cash expense")}

# ---------------- trained models ----------------
_cache = {}
def load(name):
    if name not in _cache:
        p = MODELS/f"{name}.joblib"
        if not p.exists(): raise FileNotFoundError(f"{p} missing - run `python train.py` first")
        _cache[name] = joblib.load(p)
    return _cache[name]
def _norm(s):
    s = re.sub(r"\b(upi|paid|payment|pos|to|order|blr|pvt|ltd)\b", " ", str(s).lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()
def feedback_lookup(desc, uid):
    """Category this user previously chose for this merchant; the newest entry wins."""
    n = _norm(desc); hit = None
    if uid is None or len(n) < 3: return None
    for d, cat in q("SELECT description,category FROM feedback WHERE user_id=? ORDER BY rowid", uid):
        k = _norm(d)
        if cat in CATS and len(k) >= 3 and (k == n or (len(k) >= 4 and len(n) >= 4 and (k in n or n in k))): hit = cat
    return hit

KEYWORDS = {
 "Food": "food lunch dinner breakfast snack snacks tea coffee chai groceries grocery meal meals restaurant tiffin vegetables fruits milk biryani pizza burger",
 "Transport": "transport bus auto taxi cab petrol diesel fuel metro train travel ticket parking toll rickshaw", "Rent": "rent",
 "Bills": "bill bills electricity recharge wifi internet broadband dth emi insurance", "Shopping": "shopping clothes shirt shoes dress jeans",
 "Entertainment": "entertainment movie movies cinema game games concert party outing", "Health": "health medicine medicines doctor hospital pharmacy medical gym clinic",
 "Education": "education school tuition books course exam stationery", "Other": "other misc miscellaneous"}
def keyword_category(desc):
    """A category word the user typed ('6000 on food') is trusted; the earliest match wins."""
    words = re.findall(r"[a-z]+", str(desc).lower())
    hits = [(words.index(k), c) for c, ks in KEYWORDS.items() for k in ks.split() if k in words]
    return min(hits)[1] if hits else None
def predict_category_ex(desc, uid=None):
    """-> (category, confidence, source). Order: typed category word > this user's earlier corrections > trained model."""
    for fn, src in ((keyword_category, "keyword"), (lambda d: feedback_lookup(d, uid), "feedback")):
        if c := fn(desc): return c, 1.0, src
    pipe = load("categorizer"); pr = pipe.predict_proba([desc])[0]; i = int(pr.argmax())
    return pipe.classes_[i], float(pr[i]), "model"
def predict_category(desc, uid=None): return predict_category_ex(desc, uid)[:2]

def overspend_risk(spent, budget, day_frac, cat):
    if budget <= 0: return 1.0
    return float(load("overspend")["model"].predict_proba([[spent/budget, day_frac, np.log1p(budget), CATS.index(cat)]])[0][1])

def _outlier(amount, ref):
    """Isolation-forest outlier that is also at least UNUSUAL_X times the usual amount."""
    amount, ref = np.asarray(amount, float), np.maximum(np.asarray(ref, float), 1.0)
    return (load("anomaly")["model"].predict(np.c_[np.log(np.maximum(amount, 1) / ref), np.log1p(amount)]) == -1) & (amount >= UNUSUAL_X * ref)
def is_anomaly(amount, cat, ref=None):
    """ref = the user's own median for this category (global median if unknown)."""
    return bool(_outlier([amount], [ref or load("anomaly")["median"].get(cat, 1.0)])[0])
def anomaly_mask(df):
    """-> (mask of unusually large payments, typical amount per row). 'Typical' = the user's own category median (global if < 8 payments)."""
    if df.empty: return pd.Series([], dtype=bool), pd.Series([], dtype=float)
    g = df.groupby("category").amount; ref = g.transform("median").where(g.transform("count") >= 8, df.category.map(load("anomaly")["median"])).fillna(1.0).clip(lower=1.0)
    return pd.Series(_outlier(df.amount.values, ref.values), index=df.index), ref

# ---------------- forecasting and projection ----------------
def fc_row(last3, month, cat):
    a = np.asarray(last3, float); m3 = a.mean() or 1.0
    return [a[-1]/m3, a[-2]/m3, a[-3]/m3, np.sin(2*np.pi*month/12), np.cos(2*np.pi*month/12), CATS.index(cat)], m3
def forecast_next(hist, month):
    """hist = DataFrame[ym, category, amount]. -> {category: predicted spend for `month`} from the last 3 months."""
    out = {}
    for cat, g in hist.groupby("category"):
        a = g.sort_values("ym").amount.values
        if len(a) >= 3: f, m3 = fc_row(a[-3:], month, cat); out[cat] = float(load("forecaster")["model"].predict([f])[0] * m3)
        else: out[cat] = float(a.mean())
    return out
def projected(spent, budgets, day, dim, forecast):
    """Month-end estimate anchored to the user's own budget: over budget -> what was spent; under budget -> assume the rest of the
    month uses at most the remaining budget (less if the forecast says so). No run-rate extrapolation, so one big purchase is not
    mistaken for a habit and a re-plan does not trigger another one."""
    w, out = min(1.0, day/dim), {}
    for c, b in budgets.items():
        s = spent.get(c, 0.0); p = s + (1-w) * max(0.0, min(forecast.get(c, b), b) - s)
        out[c] = max(s, p)
    return out
def month_plan(uid, ym, today=None):
    """Plan table for one month: starting budget, current budget, spent, and the optimiser's new budget."""
    pl = get_plan_eff(uid, ym)
    if not pl: return None
    income, goal, budgets = pl; base = get_plan(uid, ym)[0]; today = today or date.today(); y, m = map(int, ym.split("-")); dim = calendar.monthrange(y, m)[1]
    cur = today.isoformat()[:7]; day = dim if ym < cur else today.day if ym == cur else 1
    spent, hist = spent_by_cat(uid, ym), monthly_history(uid, before=ym)
    proj = projected(spent, budgets, day, dim, forecast_next(hist, m) if len(hist) and hist.ym.nunique() >= 3 else {})
    plan, start, rows = rebalance(budgets, spent, proj, income, goal), get_start_plan(uid, ym) or budgets, []
    for c, cu in budgets.items():
        s = spent.get(c, 0.0); new = plan["budgets"][c] if plan else cu
        rows.append({"Category": c, "Starting budget": round(start.get(c, cu)), "Current budget": round(cu), "Spent so far": round(s), "Left": round(cu - s), "New budget": new, "Change": new - round(cu),
                     "Status": "Over budget" if s > cu else "Increased" if new > cu else "Reduced to fund others" if new < cu else "On track"})
    material = sum(abs(x["delta"]) for x in plan["changes"]) >= max(100, .01 * income)      # ignore tiny leftovers
    return dict(rows=rows, plan=plan, needs_replan=material and ym >= cur, proj=proj, income=income, base_income=base, received=income - base, goal=goal, budgets=budgets, spent=spent)

# ---------------- budget optimiser ----------------
def _spread(old, lo, w, R, idx):
    """Share a total cut R over the categories in idx in proportion to w, never going below lo (water-filling)."""
    room = {i: max(old[i] - lo[i], 0.) for i in idx}
    if sum(room.values()) < R - 1e-6: return {}
    cut, active = dict.fromkeys(idx, 0.), [i for i in idx if room[i] > 1e-9]
    while R > 1e-6 and active:
        tw = sum(w[i] for i in active); sat = [i for i in active if cut[i] + R*w[i]/tw >= room[i] - 1e-9]
        if not sat:
            for i in active: cut[i] += R*w[i]/tw
            break
        for i in sat: R -= room[i] - cut[i]; cut[i] = room[i]
        active = [i for i in active if i not in sat]
    return cut
def rebalance(budgets, spent, proj, income, goal, sav_pen=SAV_PEN):
    """Always returns a plan. Linear program: minimise  sum(increase) + sum(cut / flexibility) + sav_pen * savings_shortfall
    subject to  new_i - old_i = increase_i - cut_i ;  sum(new) + savings = income ;  savings + shortfall >= goal ;  new_i >= lower_i.
    Fixed costs barely move, necessities move a little and keep a minimum, optional spending takes most of the cut. Lower bounds go from
    strict (never below projected need) to emergency (only spent / minimum), stopping at the gentlest one that meets the savings goal; if even that is impossible (already spent more than income)
    the plan is 'deficit': each budget equals what was spent (or its minimum) and savings are negative."""
    cats = list(budgets); n = len(cats); vec = lambda d, z=0.: np.array([d.get(k, z) for k in cats], float)
    old, sp = vec(budgets), vec(spent); pr = np.maximum(vec(proj), sp); fl = np.array([FLOOR_PCT.get(k, 0) * budgets[k] for k in cats]); fx = np.maximum(vec(FLEX, .5), .02)
    need, cap, I = np.where(pr > old, pr, 0), .4 * old, np.eye(n)      # cap: never cut a category by more than 60% (except in emergency)
    Aeq = np.block([[I, -I, I, np.zeros((n, 2))], [np.ones((1, n)), np.zeros((1, 2*n)), np.array([[1, 0]])]]); beq = np.r_[old, income]
    Aub = np.r_[np.zeros(3*n), -1, -1][None]; c = np.r_[np.zeros(n), np.ones(n), 1/fx, 0, sav_pen]
    best = None
    for mode, lo in (("strict", np.maximum.reduce([sp, fl, pr, cap])), ("relaxed", np.maximum.reduce([sp, fl, need, cap])),
                     ("half-need", np.maximum.reduce([sp, fl, .5*pr, cap])), ("emergency", np.maximum(sp, fl))):
        r = linprog(c, A_ub=Aub, b_ub=[-goal], A_eq=Aeq, b_eq=beq, bounds=[(l, None) for l in lo] + [(0, None)] * (2*n + 2), method="highs")
        if r.success and (best is None or r.x[3*n+1] < best[0].x[3*n+1] - .5): best = (r, mode, lo)    # keep the setting that misses the goal least
        if r.success and r.x[3*n+1] <= .5: break                                                      # goal met: stop at the gentlest setting that does it
    if best:
        r, mode, lo = best; new, s, sh = r.x[:n].copy(), r.x[3*n], r.x[3*n+1]; R = float((old - new)[new < old - .5].sum())
        if R > .5:
            for i, v in _spread(old, lo, fx * old, R, [i for i in range(n) if new[i] <= old[i] + .5]).items(): new[i] = old[i] - v
    else: mode, new = "deficit", np.maximum(sp, fl); s = income - new.sum(); sh = max(goal - s, 0.)
    return dict(mode=mode, budgets={k: round(float(v)) for k, v in zip(cats, new)}, savings=round(float(s)), shortfall=round(float(sh)),
                changes=[dict(category=k, old=round(o), new=round(float(v)), delta=round(float(v - o))) for k, o, v in zip(cats, old, new) if abs(v - o) >= 1])