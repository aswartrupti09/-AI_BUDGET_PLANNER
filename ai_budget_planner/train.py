"""Trains + evaluates all ML models; writes models/*.joblib and models/metrics.json"""
import json, os
import numpy as np, pandas as pd, joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor, GradientBoostingClassifier, IsolationForest
from sklearn.metrics import accuracy_score, f1_score, classification_report, mean_absolute_error, roc_auc_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from data_gen import generate
from core import CATS, MODELS, fc_row

M = {}
tx = generate(); tx.to_csv("data/transactions.csv", index=False); tx["ym"] = tx.date.str[:7]
MODELS.mkdir(exist_ok=True); os.makedirs("data", exist_ok=True)

# 1. Expense categorizer -------------------------------------------------
Xtr, Xte, ytr, yte = train_test_split(tx.description, tx.category, test_size=.2, random_state=0, stratify=tx.category)
import sqlite3   # users' own corrections (saved in the app's database) are strong labels: add them (x30) to the training split only
try: fb = pd.read_sql("SELECT description, category FROM feedback", sqlite3.connect("planner.db")).dropna()
except Exception: fb = pd.DataFrame(columns=["description", "category"])
fb = fb[fb.category.isin(CATS)]
if len(fb): Xtr = pd.concat([Xtr] + [fb.description] * 30); ytr = pd.concat([ytr] + [fb.category] * 30); print(f"+ {len(fb)} feedback rows")
import importer   # optional real dataset: put any expense CSV at data/real.csv (columns are detected, labels mapped by importer.py)
REAL = importer.parse_csv("data/real.csv") if os.path.exists("data/real.csv") else None
real_eval = None
if REAL is not None:
    rc = REAL[(REAL.kind == "expense") & REAL.category.isin(CATS) & REAL.description.notna()].drop_duplicates(["description", "category"]).copy()
    if len(rc) >= 30:
        # whole merchants go to train OR test, so the score measures UNSEEN shops
        from core import _norm
        grp = rc.description.map(_norm).replace("", "unknown")
        if grp.nunique() >= 10:
            from sklearn.model_selection import StratifiedGroupKFold   # merchants stay together AND every category appears in train and test
            i_tr, i_te = next(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0).split(rc, rc.category, groups=grp))
            Rtr, Rytr, Rte, Ryte = rc.description.iloc[i_tr], rc.category.iloc[i_tr], rc.description.iloc[i_te], rc.category.iloc[i_te]
        else: Rtr, Rte, Rytr, Ryte = train_test_split(rc.description, rc.category, test_size=.2, random_state=0)
        Xtr0, ytr0 = Xtr, ytr   # synthetic-only baseline, to show the gap between fake and real data
        reps = max(1, min(20, int(len(Xtr0) * .3 / len(Rtr)))); Xtr = pd.concat([Xtr] + [Rtr] * reps); ytr = pd.concat([ytr] + [Rytr] * reps)
        real_eval = (Rte, Ryte, make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4)), LogisticRegression(max_iter=1000, C=5)).fit(Xtr0, ytr0))
        print(f"+ {len(rc)} real labelled rows ({len(Rtr)} train x{reps}, {len(Rte)} test)")
res = {}
for name, clf in {"LogisticRegression": LogisticRegression(max_iter=1000, C=5), "LinearSVC": LinearSVC()}.items():
    p = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4)), clf).fit(Xtr, ytr); pr = p.predict(Xte)
    res[name] = dict(accuracy=accuracy_score(yte, pr), macro_f1=f1_score(yte, pr, average="macro"))
cat = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4)), LogisticRegression(max_iter=1000, C=5)).fit(Xtr, ytr)  # proba needed for confidence
conf = cat.predict_proba(Xte).max(1); hi = conf >= .6
res["deployed_LogReg_conf>=0.6"] = dict(coverage=float(hi.mean()), accuracy_on_covered=accuracy_score(yte[hi], cat.predict(Xte)[hi]))
if real_eval:
    Rte, Ryte, base = real_eval; pr = cat.predict(Rte)
    res["on_REAL_data"] = dict(rows=len(Rte), accuracy=accuracy_score(Ryte, pr), macro_f1=f1_score(Ryte, pr, average="macro"),
                               synthetic_only_model_accuracy=accuracy_score(Ryte, base.predict(Rte)))
    print("REAL data, held-out:\n" + classification_report(Ryte, pr, zero_division=0))
M["categorizer"] = res; joblib.dump(cat, MODELS/"categorizer.joblib")
print(classification_report(yte, cat.predict(Xte)))

# 2. Forecaster ----------------------------------------------------------
mon = tx.groupby(["user","ym","category"], as_index=False).amount.sum(); rows = []
for (u,c), g in mon.groupby(["user","category"]):
    g = g.sort_values("ym"); a = g.amount.values; mo = g.ym.str[5:].astype(int).values
    for t in range(3, len(a)):
        f, m3 = fc_row(a[t-3:t], mo[t], c); rows.append((t >= 20, f, m3, a[t]))
n_real = 0
if REAL is not None:
    rr = REAL[(REAL.kind == "expense") & REAL.category.isin(CATS)].dropna(subset=["date"]).copy(); rr["ym"] = rr.date.str[:7]
    for (u, c), g in rr.groupby(["user", "category"]):
        g = g.groupby("ym").amount.sum(); full = pd.period_range(g.index.min(), g.index.max(), freq="M")
        a = g.reindex(full.astype(str), fill_value=0.0).values; mo = np.array([p.month for p in full])
        if len(a) < 7: continue
        for t in range(3, len(a)):
            if a[t-3:t].sum() <= 0: continue
            f, m3 = fc_row(a[t-3:t], mo[t], c); rows.append((t >= len(a) - 3, f, m3, a[t])); n_real += 1
    print(f"+ {n_real} real monthly forecast examples")
te = np.array([r[0] for r in rows], bool); tr = ~te; F = np.vstack([r[1] for r in rows]); m3 = np.array([r[2] for r in rows]); y = np.array([r[3] for r in rows])
def ev(pred): return dict(MAE=float(mean_absolute_error(y[te], pred)), MAPE=float(np.mean(np.abs(y[te]-pred)/np.maximum(y[te], 1))*100))
fres = {"naive_last_month": ev(F[te,0]*m3[te]), "mean_last_3": ev(m3[te])}; best = None
for name, mdl in {"GradientBoosting": GradientBoostingRegressor(n_estimators=150, max_depth=3, learning_rate=.05, random_state=0),
                  "RandomForest": RandomForestRegressor(n_estimators=150, min_samples_leaf=3, random_state=0)}.items():
    mdl.fit(F[tr], y[tr]/m3[tr]); fres[name] = ev(mdl.predict(F[te])*m3[te])
    if best is None or fres[name]["MAE"] < fres[best[0]]["MAE"]: best = (name, mdl)
M["forecaster"] = dict(results=fres, deployed=best[0], real_examples=n_real); joblib.dump({"model": best[1]}, MODELS/"forecaster.joblib")

# 3. Overspend-risk classifier ------------------------------------------
rng = np.random.default_rng(1); tx["day"] = tx.date.str[8:].astype(int); rows = []
typical = mon.groupby(["user","category"]).amount.mean()
for (u, ym, c), g in tx.groupby(["user","ym","category"]):
    total = g.amount.sum(); b = typical[(u,c)] * rng.uniform(.9, 1.1)
    for d in (5,10,15,20,25):
        s = g.amount[g.day <= d].sum(); rows.append((u, s/b, d/30, np.log1p(b), CATS.index(c), int(total > b)))
R = pd.DataFrame(rows, columns=["user","sf","df","lb","c","y"]); users = R.user.unique(); trn = R.user.isin(users[:int(len(users)*.8)])
clf = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=0).fit(R[trn][["sf","df","lb","c"]], R[trn].y)
te_ = R[~trn]; pp = clf.predict_proba(te_[["sf","df","lb","c"]])[:,1]; rule = te_.sf/te_.df
M["overspend"] = dict(accuracy=accuracy_score(te_.y, pp > .5), auc=roc_auc_score(te_.y, pp), baseline_runrate_auc=roc_auc_score(te_.y, rule))
joblib.dump({"model": clf}, MODELS/"overspend.joblib")

# 4. Anomaly detector (amount relative to the user's own median in that category) -----
um = tx.groupby(["user","category"]).amount.transform("median"); med = tx.groupby("category").amount.median().to_dict()
Z = np.c_[np.log(tx.amount/um), np.log1p(tx.amount)]
iso = IsolationForest(n_estimators=200, contamination=.02, random_state=0).fit(Z); pr = iso.predict(Z) == -1
p, r, f, _ = precision_recall_fscore_support(tx.is_anomaly, pr, average="binary")
M["anomaly"] = dict(precision=p, recall=r, f1=f); joblib.dump({"model": iso, "median": med}, MODELS/"anomaly.joblib")

M["data"] = dict(synthetic_rows=len(tx), real_rows=0 if REAL is None else len(REAL))
(MODELS/"metrics.json").write_text(json.dumps(M, indent=1, default=float)); print(json.dumps(M, indent=1, default=float))