"""LangGraph pipeline with four agents (per user, per month):
capture -> [unsure? ask the user] -> record -> monitor -> [over budget?] -> planner"""
import calendar
from datetime import date
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
import core, payment_ocr as ocr

class S(TypedDict, total=False):
    uid: int; text: str; image: bytes; on_date: str; txn: dict; confirmed: bool; need_confirm: bool; income_added: bool
    status: dict; change: dict; log: list

def _log(s, m): return (s.get("log") or []) + [m]

def capture_agent(s):
    """Reads a typed line or a screenshot, decides paid vs received, and picks the category (typed word > user's past choice > model)."""
    if s.get("image"): t = ocr.extract(s["image"])
    else: t = core.parse_cash_text(s.get("text", "")); t["date"] = s.get("on_date") or t["date"]
    log = _log(s, f"Capture agent read: amount {t.get('amount')}, '{t['description']}', {t['date']} ({t['kind']})")
    if not t.get("amount") or t["kind"] == "income": return {"txn": t, "log": log}
    cat, conf, src = core.predict_category_ex(t["description"], s["uid"]); t.update(category=cat, confidence=conf)
    how = {"keyword": "from the category word you typed", "feedback": "from your earlier correction", "model": "trained model"}[src]
    return {"txn": t, "need_confirm": conf < .6, "log": log + [f"Capture agent chose {cat} ({conf:.0%}, {how})" + (" - not sure, asking you" if conf < .6 else "")]}

def record_agent(s):
    """Saves the expense, or adds received money to that month's income."""
    inc = s["txn"]["kind"] == "income"; t = {**s["txn"], **({"category": "Income"} if inc else {})}; core.add_txn(s["uid"], t)
    return {"txn": t, "income_added": inc, "log": _log(s, f"Record agent saved {t['amount']:,.0f} as {'income' if inc else 'an expense'}")}

def monitor_agent(s):
    uid, t = s["uid"], s["txn"]; ym = t["date"][:7]; pl = core.get_plan_eff(uid, ym)
    if not pl:
        return {"status": dict(rows={}, alerts=[], need_rebalance=False, anomaly=False, proj={}, spent={}, ym=ym, no_plan=True), "log": _log(s, f"Monitor agent: no budget set for {ym}")}
    income, goal, budgets = pl; y, m = map(int, ym.split("-")); dim = calendar.monthrange(y, m)[1]; cur = date.today().isoformat()[:7]
    day = dim if ym < cur else date.today().day if ym == cur else 1
    spent = core.spent_by_cat(uid, ym); hist = core.monthly_history(uid, before=ym)
    fc = core.forecast_next(hist, m) if len(hist) and hist.ym.nunique() >= 3 else {}
    proj = core.projected(spent, budgets, day, dim, fc); rows, alerts, need = {}, [], False
    for c, b in budgets.items():
        sp = spent.get(c, 0.0); pct = sp/b if b else 0; risk = core.overspend_risk(sp, b, day/dim, c)
        rows[c] = dict(spent=sp, budget=b, pct=pct, risk=risk, projected=proj[c])
        if sp > b + .5: alerts.append(f"{c} is over budget: spent ₹{sp:,.0f} of ₹{b:,.0f}."); need = True
        elif pct >= .8: alerts.append(f"{c} has used {pct:.0%} of its budget.")
        if pct < 1 and risk >= .6:
            alerts.append(f"{c} has a {risk:.0%} chance of going over budget this month.")      # a warning only; the plan changes when spending actually exceeds a budget
    allt = core.txns(uid); h = allt[allt.category == t["category"]].amount
    anom = core.is_anomaly(t["amount"], t["category"], float(h.median()) if len(h) >= 8 else None)
    if anom: alerts.append(f"₹{t['amount']:,.0f} is unusually large for {t['category']}.")
    return {"status": dict(rows=rows, alerts=alerts, need_rebalance=need, anomaly=anom, proj=proj, spent=spent, ym=ym), "log": _log(s, f"Monitor agent found {len(alerts)} alert(s)")}

def planner_agent(s):
    """Re-plans the month when spending breaks the budget: the optimiser changes the budgets, the reasoning is written, and both are saved so the change can be shown, explained and undone."""
    t = s["txn"]; ch = core.replan(s["uid"], s["status"]["ym"], f"{t['description']} (₹{t['amount']:,.0f})")
    return {"change": ch, "log": _log(s, "Planner agent changed the budgets and wrote the reasons" if ch else "Planner agent: the budgets already fit")}

def _build():
    g = StateGraph(S)
    for f in (capture_agent, record_agent, monitor_agent, planner_agent): g.add_node(f.__name__, f)
    g.add_conditional_edges(START, lambda s: "record_agent" if s.get("confirmed") else "capture_agent", ["record_agent", "capture_agent"])
    g.add_conditional_edges("capture_agent", lambda s: END if not s["txn"].get("amount") or s.get("need_confirm") else "record_agent", ["record_agent", END])
    g.add_conditional_edges("record_agent", lambda s: END if s["income_added"] else "monitor_agent", ["monitor_agent", END])
    g.add_conditional_edges("monitor_agent", lambda s: "planner_agent" if s["status"]["need_rebalance"] else END, ["planner_agent", END])
    g.add_edge("planner_agent", END)
    return g.compile()
GRAPH = _build()

def run(uid, text=None, image=None, on_date=None): return GRAPH.invoke({"uid": uid, "text": text or "", "image": image, "on_date": on_date})
def confirm(uid, txn, category):
    """Human in the loop: the user fixes the category; it is remembered for this user and used when retraining."""
    core.add_feedback(uid, txn["description"], category)
    return GRAPH.invoke({"uid": uid, "txn": {**txn, "category": category, "confidence": 1.0, "kind": "expense"}, "confirmed": True})
def add_income(uid, txn): return GRAPH.invoke({"uid": uid, "txn": {**txn, "kind": "income"}, "confirmed": True})