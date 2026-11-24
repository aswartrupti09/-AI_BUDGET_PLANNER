import calendar
from datetime import date
import pandas as pd, plotly.express as px, streamlit as st
import core, agents, payment_ocr, reasoning, importer
from data_gen import CATS

core.init_db(); st.set_page_config("AI Budget Planner", "💰", layout="wide")
DEFAULT = dict(Food=7000, Transport=3000, Rent=15000, Bills=2500, Shopping=4000, Entertainment=2500, Health=1000, Education=500, Other=1000)

# ======================= login / register =======================
if "uid" not in st.session_state:
    st.title("💰 AI Budget Planner")
    st.caption("Log in or create an account. Each person's budgets and expenses are kept separate.")
    tl, tr = st.tabs(["Log in", "Register"])
    with tl, st.form("login"):
        u = st.text_input("Username"); p = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            uid = core.login(u, p)
            if uid: st.session_state.update(uid=uid, user=u.strip().lower()); st.rerun()
            else: st.error("Wrong username or password.")
    with tr, st.form("register"):
        u = st.text_input("Choose a username"); p = st.text_input("Choose a password (6+ characters)", type="password"); p2 = st.text_input("Repeat password", type="password")
        if st.form_submit_button("Create account", type="primary"):
            if p != p2: st.error("The two passwords don't match.")
            else:
                uid, msg = core.register(u, p)
                if uid: st.session_state.update(uid=uid, user=u.strip().lower()); st.rerun()
                else: st.error(msg)
    st.stop()

uid = st.session_state.uid; today = date.today(); cur_ym = today.isoformat()[:7]
def ym_add(ym, k): y, m = map(int, ym.split("-")); t = y*12 + m - 1 + k; return f"{t//12}-{t%12+1:02d}"
def label(ym): return f"{calendar.month_name[int(ym[5:])]} {ym[:4]}"
summ = core.month_summary(uid)
months = sorted({ym_add(cur_ym, k) for k in range(-11, 2)} | (set(summ.Month) if len(summ) else set()))

# ======================= sidebar: month + that month's budget =======================
with st.sidebar:
    st.write(f"👤 **{st.session_state.user}**")
    if st.button("Log out"):
        for k in list(st.session_state): del st.session_state[k]
        st.rerun()
    ym = st.selectbox("Month", months, index=months.index(cur_ym), format_func=label)
    plan = core.get_plan(uid, ym); prev = core.last_plan_ym(uid, ym); src = plan or (core.get_plan(uid, prev) if prev else None)
    st.header(f"Budget for {label(ym)}")
    if not plan and src: st.caption(f"Pre-filled from {label(prev)}. Change anything, then save.")
    inc = st.number_input("Income / total money (₹)", 0, value=int(src[0]) if src else 40000, step=1000, key=f"inc_{ym}")
    st.caption("Money you receive later (\"Received from…\") is added to this automatically.")
    goal = st.number_input("Savings goal (₹)", 0, value=int(src[1]) if src else 8000, step=500, key=f"goal_{ym}")
    dflt = src[2] if src else DEFAULT
    bud = {c: st.number_input(c, 0, value=int(dflt.get(c, 0)), step=100, key=f"b_{ym}_{c}") for c in CATS}
    total = sum(bud.values()) + goal
    st.caption(f"Budgets + savings: ₹{total:,} / Income: ₹{inc:,}")
    if total > inc: st.error(f"Over income by ₹{total-inc:,}. Reduce budgets or raise income.")
    elif total < inc: st.info(f"₹{inc-total:,} is unallocated and will count as extra savings.")
    if st.button(f"Save plan for {label(ym)}", type="primary", disabled=total > inc): core.save_plan(uid, ym, inc, goal, bud); st.rerun()
    with st.expander("About the models (for evaluators)"): st.json(core.load_metrics())

try: core.load("categorizer")
except FileNotFoundError: st.error("Models not trained. Run `python train.py` first."); st.stop()

# ======================= helpers =======================
def reasoning_view(r):
    with st.expander("Why this plan? (in simple points)", expanded=True):
        for title, pts in r["steps"]: st.markdown(f"**{title}**\n\n" + "\n".join(f"- {p}" for p in pts))
        if r["checks"]:
            icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
            st.markdown("**Double-check**\n\n" + "\n".join(f"- {icon[s]} {t}" for t, s in r["checks"]))
        st.info(r["recommendation"])

def plan_view(pym, key):
    mp = core.month_plan(uid, pym)
    if not mp: st.info(f"No budget set for {label(pym)} yet."); return
    st.dataframe(pd.DataFrame(mp["rows"]), hide_index=True, width="stretch"); p = mp["plan"]
    if not p: st.error("No possible plan: spending is higher than income even after cutting flexible categories."); return
    reasoning_view(reasoning.build_reasoning(mp["budgets"], mp["spent"], mp["proj"], mp["income"], mp["goal"], core.load_flex(), p))
    st.write(f"Savings after this plan: **₹{p['savings']:,}**" + (f" (₹{p['shortfall']:,} short of your ₹{mp['goal']:,} goal)" if p["shortfall"] else " (goal protected ✅)"))
    if p["changes"]:
        if st.button("Apply this plan", key=f"apply_{key}"): core.update_budgets(uid, pym, p["budgets"]); st.session_state.pop("res", None); st.rerun()
    else: st.caption("Nothing needs to change right now.")

def show(res):
    t = res.get("txn", {})
    if t.get("amount") is None: st.error("I couldn't find an amount. Try something like 'spent 200 on tea'."); return
    if res.get("income_added"):
        m_ = t["date"][:7]; got = core.received_by_month(uid, m_); pl_ = core.get_plan(uid, m_)
        st.success(f"Added ₹{t['amount']:,.0f} received ({t['description']}) to your income for {label(m_)}.")
        st.info(f"Income for {label(m_)}: ₹{pl_[0]:,.0f} planned + ₹{got:,.0f} received = ₹{pl_[0]+got:,.0f}." if pl_ else f"There's no budget for {label(m_)} yet. The money is saved and will count once you set a plan for that month.")
        with st.expander("Technical details: how the agents handled this"): st.markdown("\n".join(f"- {l}" for l in res.get("log", [])))
        return
    if res.get("need_confirm"):
        st.warning(f"I'm not sure about “{t['description']}” (₹{t['amount']:,.0f}). Which category is it?")
        c = st.selectbox("Category", CATS, index=CATS.index(t["category"]), key="conf_cat")
        if st.button("Confirm and save"): st.session_state.res = agents.confirm(uid, t, c); st.rerun()
        return
    st.success(f"Saved ₹{t['amount']:,.0f} under **{t['category']}** on {t['date']}.")
    stt = res.get("status", {})
    if stt.get("no_plan"): st.info(f"There's no budget for {label(stt['ym'])} yet. Choose that month in the sidebar and save a plan to track it.")
    for a in stt.get("alerts", []): st.warning(a)
    if res.get("advice"): st.markdown(res["advice"])
    if res.get("plan") or (stt.get("need_rebalance") and not res.get("plan")): st.subheader("Suggested plan"); plan_view(stt["ym"], "after")
    with st.expander("Technical details: how the agents handled this"): st.markdown("\n".join(f"- {l}" for l in res.get("log", [])))

def default_date():
    if ym == cur_ym: return today
    y, m = map(int, ym.split("-")); return date(y, m, calendar.monthrange(y, m)[1] if ym < cur_ym else 1)

st.title(f"💰 {label(ym)}")
t1, t2, t3 = st.tabs(["➕ Add expense", "📊 This month", "📅 Months & forecast"])

# ======================= add expense =======================
with t1:
    mode = st.radio("How do you want to add it?", ["Type it (cash)", "Payment screenshot"], horizontal=True)
    if mode == "Type it (cash)":
        c1, c2 = st.columns([3, 1]); txt = c1.text_input("e.g. spent 200 on tea"); d = c2.date_input("Date", value=default_date(), key=f"d_{ym}")
        if st.button("Add") and txt: st.session_state.res = agents.run(uid, text=txt, on_date=d.isoformat())
    else:
        f = st.file_uploader("GPay / PhonePe / Paytm screenshot", type=["png", "jpg", "jpeg"])
        if f and st.button("Read screenshot"):
            st.session_state.pop("shot", None); st.session_state.shot = payment_ocr.extract(f.getvalue()); st.session_state.pop("res", None)
        if "shot" in st.session_state:
            s = st.session_state.shot; st.info("Check what was read, fix anything wrong, then save.")
            if s.get("error"): st.error(s["error"])
            if s.get("auto_fixed"): st.success(f"The ₹ sign was misread and has been corrected to ₹{s['amount']:,.0f}.")
            opts = [s.get("amount") or 0.0] + ([s["alt_amount"]] if s.get("alt_amount") else []); pick = opts[0]
            if len(opts) > 1:
                st.warning(f"Read ₹{opts[0]:,.0f}, but the ₹ sign may have been read as a digit. Which is right?")
                pick = st.radio("Amount", opts, format_func=lambda v: f"₹{v:,.0f}", horizontal=True)
            a1, a2, a3 = st.columns(3)
            amt = a1.number_input("Amount (₹)", 0.0, value=float(pick), step=10.0, key=f"amt_{pick}")
            mer = a2.text_input("Merchant / description", s["description"])
            try: dd = date.fromisoformat(s["date"])
            except ValueError: dd = today
            d = a3.date_input("Date", value=dd)
            kind = st.radio("What was this?", ["Money I paid (expense)", "Money I received (income)"], index=1 if s.get("kind") == "income" else 0, horizontal=True)
            got = kind.startswith("Money I received"); cat = None
            if got: st.caption("This will be added to your income for that month.")
            else:
                pc, pf, src_ = core.predict_category_ex(mer, uid); st.caption(f"Suggested: {pc} ({pf:.0%})" + (" - remembered from your earlier choice" if src_ == "feedback" else ""))
                cat = st.selectbox("Category", CATS, index=CATS.index(pc))
            if st.button("Save", type="primary") and amt > 0:
                if len(opts) > 1: payment_ocr.learn_lead(str(int(opts[0]))[0] if pick != opts[0] else False)
                t_ = {**s, "amount": amt, "description": mer, "date": d.isoformat()}
                st.session_state.res = agents.add_income(uid, t_) if got else agents.confirm(uid, t_, cat); st.session_state.pop("shot"); st.rerun()
            with st.expander("Technical details (screenshot reading)"):
                st.write(f"Image check: {s.get('glyph_info', 'not run')} | parser {payment_ocr.VERSION}"); st.code(s.get("raw_text") or "(nothing read)")
    if "res" in st.session_state: show(st.session_state.res)

# ======================= this month =======================
with t2:
    mp = core.month_plan(uid, ym); d_all = core.txns(uid, kind=None); cur = d_all[d_all.date.str.startswith(ym)].sort_values("date", ascending=False)
    if not mp: st.info(f"Set the budget for {label(ym)} in the sidebar and click **Save plan**.")
    else:
        spent, budgets, income, goal = mp["spent"], mp["budgets"], mp["income"], mp["goal"]; ts = sum(spent.values())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Spent", f"₹{ts:,.0f}"); c2.metric("Budget left", f"₹{sum(budgets.values())-ts:,.0f}")
        c3.metric("Saved so far", f"₹{income-ts:,.0f}"); c4.metric("Savings goal", f"₹{goal:,.0f}")
        df = pd.DataFrame({"Category": CATS, "Budget": [budgets.get(c, 0) for c in CATS], "Spent": [spent.get(c, 0) for c in CATS]}).melt("Category", var_name="Type", value_name="₹")
        if mp["received"] > 0: st.caption(f"Income ₹{income:,.0f} = planned ₹{mp['base_income']:,.0f} + received ₹{mp['received']:,.0f}.")
        st.plotly_chart(px.bar(df, x="Category", y="₹", color="Type", barmode="group"), width="stretch")
        if ym >= cur_ym:
            st.subheader("Plan for the rest of the month"); plan_view(ym, "dash")
            with st.expander("What-if: change one budget and see the effect"):
                wc = st.selectbox("Category", CATS, key="wi_c"); wp = st.slider("Change budget by (%)", -60, 60, -20, key="wi_p")
                nb = {**budgets, wc: round(budgets[wc] * (1 + wp/100))}; sav = income - sum(nb.values()); sp_ = spent.get(wc, 0)
                st.write(f"{wc}: ₹{budgets[wc]:,} → **₹{nb[wc]:,}**. Savings would be **₹{sav:,}** ({'meets' if sav >= goal else 'misses'} your ₹{goal:,} goal" + ("" if sav >= goal else f" by ₹{goal-sav:,}") + ").")
                if nb[wc] < sp_: st.warning(f"That is below the ₹{sp_:,.0f} you already spent on {wc}.")
        else: st.caption("This month is over, so no new plan is suggested. See the results above.")
    kinds = cur.kind.fillna("expense"); ex = cur[kinds == "expense"].drop(columns=["user_id", "kind"]); rc = cur[kinds == "income"].drop(columns=["user_id", "kind", "category"])
    st.subheader("Expenses this month"); st.dataframe(ex, hide_index=True, width="stretch")
    if len(rc): st.subheader("Money received this month"); st.dataframe(rc, hide_index=True, width="stretch")
    if len(cur):
        did = st.selectbox("Delete a wrong entry", cur.id, format_func=lambda i: f"#{i}  ₹{cur.set_index('id').amount[i]:,.0f}  {cur.set_index('id').description[i]}")
        if st.button("Delete entry"): core.delete_txn(uid, did); st.rerun()

# ======================= months & forecast =======================
with t3:
    summ = core.month_summary(uid)
    if len(summ):
        st.subheader("How much you saved each month")
        st.caption("Saved = income minus what you spent. The current month is still in progress, so its number will go down as you add expenses.")
        s2 = summ.dropna(subset=["Saved"]).copy(); s2["Month"] = [label(m) + (" (so far)" if m == cur_ym else "") for m in s2.Month]
        if len(s2):
            fig = px.bar(s2.melt("Month", ["Saved", "Goal"], var_name="Type", value_name="₹"), x="Month", y="₹", color="Type", barmode="group")
            fig.update_xaxes(type="category"); st.plotly_chart(fig, width="stretch")
            done = summ[(summ.Month < cur_ym) & summ.Saved.notna()]
            st.metric("Total saved in finished months", f"₹{done.Saved.sum():,.0f}" if len(done) else "No finished month yet")
        view = summ.copy(); view["Goal met"] = view["Goal met"].map({True: "✅", False: "❌"}).fillna("-"); st.dataframe(view, hide_index=True, width="stretch")
    else: st.info("Add a monthly plan and some expenses to see your savings history here.")

    st.subheader("Spending forecast")
    h = core.monthly_history(uid); h = h[h.ym < cur_ym] if len(h) else h   # only finished months
    if len(h) and h.ym.nunique() >= 3:
        last = h.ym.max(); nxt_ym = ym_add(last, 1); fc = core.forecast_next(h, int(nxt_ym[5:]))
        st.caption(f"Based on finished months up to {label(last)}. Forecast is for {label(nxt_ym)}.")
        st.plotly_chart(px.line(h, x="ym", y="amount", color="category", labels={"ym": "Month"}, title="Spending by category"), width="stretch")
        pl = core.get_plan(uid, nxt_ym) or core.get_plan(uid, core.last_plan_ym(uid, nxt_ym) or "")
        sug = core.rebalance(fc, {}, {}, pl[0], pl[1], core.load_flex()) if pl else None
        out = pd.DataFrame({"Category": CATS, f"Forecast {calendar.month_name[int(nxt_ym[5:])]}": [round(fc.get(c, 0)) for c in CATS],
                            "Suggested budget": [sug["budgets"].get(c) if sug else None for c in CATS]})
        st.dataframe(out, hide_index=True, width="stretch")
        if sug and st.button(f"Use these as my {label(nxt_ym)} budget"):
            core.save_plan(uid, nxt_ym, pl[0], pl[1], sug["budgets"]); st.success(f"Saved. Select {label(nxt_ym)} in the sidebar to see it."); 
    else: st.info("The forecast needs at least 3 finished months of expenses. You can import past expenses below to try it.")
    with st.expander("Import past expenses"):
        up = st.file_uploader("Upload a bank / UPI statement (CSV or PDF)", type=["csv", "pdf"])
        if up:
            try: df = importer.parse(up)
            except Exception as e: df = None; st.error(str(e))
            if df is not None and df.empty:
                st.warning("I couldn't find any expenses in this file.")
                if up.name.lower().endswith(".pdf"):
                    try: txt = importer.read_pdf_text(up)
                    except Exception: txt = ""
                    if not txt.strip(): st.info("This PDF has no readable text. It may be a scanned image or password-protected. Try downloading a CSV or a text-based PDF instead.")
                    else:
                        with st.expander("Text I could read from this PDF (send this if you need help)", expanded=True): st.code(txt[:3000])
            elif df is not None:
                if "category" not in df: df["category"] = None
                if "kind" not in df: df["kind"] = "expense"
                df["category"] = [c if c in CATS else core.predict_category(x, uid)[0] for c, x in zip(df.category, df.description)]
                df.insert(0, "Include", True); st.caption("Check the rows below: untick anything that isn't an expense and fix categories, then import.")
                ed = st.data_editor(df, hide_index=True, width="stretch", key=f"imp_{up.name}_{up.size}",
                                    column_config={"category": st.column_config.SelectboxColumn("category", options=CATS), "kind": st.column_config.SelectboxColumn("kind", options=["expense", "income"])})
                keep = ed[ed.Include]
                if st.button(f"Import {len(keep)} expenses"): core.import_df(uid, keep[["date", "description", "amount", "category", "kind"]]); st.success(f"Imported {len(keep)} expenses."); st.rerun()
        if st.button("Load 24 months of sample history (demo)"):
            demo = pd.read_csv("data/transactions.csv").query("user=='salaried_0'")[["date", "description", "amount", "category"]]
            core.import_df(uid, demo, "demo"); st.rerun()