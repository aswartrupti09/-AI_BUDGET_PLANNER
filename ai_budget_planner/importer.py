"""Turn an uploaded CSV or PDF statement into rows: date, description, amount. Always previewed by the user before saving."""
import re
import pandas as pd

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s*[A-Za-z]{3,9}\.?,?\s*\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})")
AMT_RE = re.compile(r"(?<![\d/])(\d{1,3}(?:,\d{2,3})*\.\d{2}|\d+\.\d{2})(?![\d/])")          # 250.00 / 1,299.50
RUPEE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.I)                          # ₹250 / Rs. 70
OUT = re.compile(r"paid to|payment to|sent to|paid at|debited|debit|paid by|dr\b", re.I)            # looks like money going out
SKIP = re.compile(r"received from|\b(cr|credit|credited|salary|refund|cashback|interest|reversal)\b", re.I)   # money coming in

def _iso(s):
    s = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", str(s)); s = re.sub(r"(?<=[A-Za-z]),(?=\d)", ", ", s)   # '05Sep,2026' -> '05 Sep, 2026'
    d = pd.to_datetime(s, dayfirst=not re.match(r"\d{4}-", s), errors="coerce")     # ISO dates are year-first; others are read day-first
    return None if pd.isna(d) else d.date().isoformat()

def _merchant(block, incoming=False):
    m = re.search(r"(?:paid to|payment to|sent to|paid at|received from)\s*:?\s*([A-Za-z0-9][^\n₹]*?)(?=\s*(?:₹|rs\.?\s*\d|debit|credit|upi|$))", block, re.I | re.M)
    if m and len(m.group(1).strip()) >= 2: return re.sub(r"\s+\d+(?:\.\d+)?$", "", re.sub(r"\s+", " ", m.group(1)).strip())
    d = re.sub(r"\s+", " ", re.sub(r"\b\d{6,}\b", " ", RUPEE_RE.sub(" ", AMT_RE.sub(" ", DATE_RE.sub(" ", block))))).strip(" -/|")
    return d[:60] if len(d) >= 2 else "expense"

def parse_lines(text):
    """Statement text -> expense rows. Each dated line starts a block (wrapped lines like 'UPI Transaction ID' belong to it).
    Handles bank rows (debit amount first) and GPay/PhonePe style blocks ('Paid to X  ₹250'). Money received is skipped."""
    blocks, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        m = DATE_RE.search(line)
        if m and _iso(m.group(1)): cur = {"date": _iso(m.group(1)), "text": line}; blocks.append(cur)
        elif cur is not None: cur["text"] += "\n" + line
    rows = []
    for b in blocks:
        t = b["text"]; money_out = bool(OUT.search(t))
        if re.search(r"period|statement|date\s*&\s*time|transaction details", t.split("\n")[0], re.I): continue   # page header, not a payment
        incoming = bool(SKIP.search(t)) and not re.search(r"paid to|payment to|sent to", t, re.I)
        r = RUPEE_RE.search(t); d = AMT_RE.search(DATE_RE.sub(" ", t))
        amt = float((r or d).group(1).replace(",", "")) if (r or d) else None
        if amt is None and money_out:   # rupee sign lost in the PDF text: take a small standalone number, ignoring times and long IDs
            n = re.findall(r"(?<![\d:.])(\d{1,6})(?![\d:.]|\s*[APap][Mm])", re.sub(r"\b\d{7,}\b", " ", DATE_RE.sub(" ", t)))
            amt = float(n[0]) if n else None
        if amt and (money_out or d or r or incoming): rows.append({"date": b["date"], "description": _merchant(t, incoming), "amount": amt, "kind": "income" if incoming else "expense"})
    return pd.DataFrame(rows, columns=["date", "description", "amount", "kind"])

def read_pdf_text(file):
    try: from pypdf import PdfReader
    except ImportError: raise RuntimeError("PDF support needs one more package. Run: pip install pypdf")
    file.seek(0); return "\n".join((p.extract_text() or "") for p in PdfReader(file).pages)

def parse_pdf(file):
    return parse_lines(read_pdf_text(file))

LABELS = {   # words a dataset's own category label may start with -> our category (earlier entries win)
 "Rent": "rent mortgage housing lease", "Bills": "bill utilit electric water internet broadband phone mobile recharge insurance emi loan subscription",
 "Education": "educat school tuition book course college fee stationery", "Health": "health medic pharmac doctor hospital fitness gym wellness dental",
 "Transport": "transport travel taxi cab uber ola fuel petrol diesel gas parking bus train metro auto vehicle car flight toll",
 "Food": "food dining restaurant grocer eating cafe coffee snack meal lunch dinner breakfast drink beverage swiggy zomato",
 "Shopping": "shop cloth apparel fashion electronic retail amazon flipkart personal gift beauty", "Entertainment": "entertain movie cinema music game leisure recreation hobby sport party streaming concert",
 "Other": "other misc uncategor atm cash transfer donat charit"}
INCOME_LABELS = "salary income deposit refund interest dividend bonus cashback paycheck wage payroll"
def map_label(label):
    """Dataset category label -> (our category or None, 'expense' | 'income'). Labels already in our list pass through."""
    w = re.findall(r"[a-z]+", str(label).lower()); hit = lambda ks: any(x.startswith(k) for x in w for k in ks.split())
    return (None, "income") if hit(INCOME_LABELS) else (next((c for c, ks in LABELS.items() if hit(ks)), None), "expense")

def parse_csv(file):
    """Any expense CSV -> date, description, amount, category, kind, user. Columns are found by name, debit/credit columns and signed
    amounts are understood, and the dataset's own category labels are mapped onto ours (unmapped ones become None)."""
    df = pd.read_csv(file, encoding_errors="replace"); low = {c: str(c).strip().lower() for c in df.columns}
    pick = lambda *ns: next((c for n in ns for c, l in low.items() if l == n), None) or next((c for n in ns for c, l in low.items() if n in l), None)
    d, a, cr = pick("date", "time"), pick("amount", "withdrawal", "debit", "expense", "value", "price"), pick("deposit", "credit")
    t, lab, u = pick("description", "narration", "details", "particulars", "merchant", "payee", "remarks", "note", "title", "name"), pick("category", "label", "class", "tag"), pick("user", "customer", "client")
    if not (d and a): raise ValueError(f"Couldn't find date and amount columns. Found: {', '.join(map(str, df.columns))}")
    num = lambda s: pd.to_numeric(s.astype(str).str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")
    out = pd.DataFrame({"date": df[d].map(_iso), "description": df[t].astype(str).str.strip() if t else "expense", "amount": num(df[a]),
                        "category": None, "kind": "expense", "user": df[u].astype(str) if u else "real"})
    if (out.amount < 0).any() and (out.amount > 0).any(): out.loc[out.amount > 0, "kind"] = "income"      # signed amounts: negative = money out
    out["amount"] = out.amount.abs()
    if lab:
        res = df[lab].map(map_label); out["category"] = [r[0] for r in res]; out.loc[[r[1] == "income" for r in res], "kind"] = "income"
    if cr and cr != a: out = pd.concat([out, out.assign(amount=num(df[cr]), kind="income", category=None)])   # separate credit column = money received
    return out.dropna(subset=["date", "amount"]).query("amount > 0")

def parse(file):
    return parse_pdf(file) if file.name.lower().endswith(".pdf") else parse_csv(file)