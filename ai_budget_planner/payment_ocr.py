"""Payment screenshot -> amount, merchant, date, paid/received. OCR + regex; the rupee sign is settled by looking at the pixels."""
import base64, io, json, os, re
from collections import Counter
from datetime import date
from pathlib import Path
import numpy as np
from importer import DATE_RE, _iso

CAL = Path(__file__).parent / "data" / "ocr_calib.json"   # remembers how this phone/OCR engine renders the rupee sign
CUR, NUM = r"(?:₹|rs\.?|inr|[?%])", r"(\d[\d,]*(?:\.\d{1,2})?)"
SKIP = r"up\s*to|limit|cashback|offer|reward|balance|secure|utr|\bid\b|ref|\d{1,2}:\d{2}"           # promo text, IDs, times
ALONE = r"[^\w\s]?\s*[zZtT]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}(?:\.\d{1,2})?)\s*[^\w\s]?"   # a line that is just a number
_num = lambda s: float(s.replace(",", ""))

def tokens(img):
    """-> [(box, text)] with box = 4 corner points. EasyOCR if installed, else Tesseract."""
    try:
        import easyocr; global _R
        _R = globals().get("_R") or easyocr.Reader(["en"], gpu=False)
        return [(b, t) for b, t, _ in _R.readtext(np.array(img))]
    except ImportError:
        import pytesseract
        d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        return [([[x, y], [x+w, y], [x+w, y+h], [x, y+h]], t) for t, x, y, w, h in zip(d["text"], d["left"], d["top"], d["width"], d["height"]) if t.strip()]

def rupee_verdict(img, box, text):
    """Count separate ink shapes in the amount's box (Rs70 = 3: sign, 7, 0) and compare with the characters OCR read.
    'digit' = same count (the sign was misread as a character) | 'dropped' = one more shape (sign left out) | None = unclear."""
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    g = np.asarray(img.convert("L"), float)[int(max(min(ys), 0)):int(max(ys)), int(max(min(xs), 0)):int(max(xs))]
    if g.size == 0 or g.shape[1] < 3 or np.ptp(g) < 30: return None, "n/a"
    d = np.abs(g - np.median(g)); cols = (d > .45 * d.max()).any(axis=0)
    n, L = int(np.sum(cols[1:] & ~cols[:-1]) + cols[0]), len(text.strip())
    return ("dropped" if n > L else "digit" if n == L else None), f"{n} shapes in image vs {L} characters read"

def learned_lead():
    try: return json.loads(CAL.read_text())["lead"]
    except Exception: return None
def learn_lead(v): CAL.parent.mkdir(exist_ok=True); CAL.write_text(json.dumps({"lead": v}))   # digit string, or False if the sign is just dropped

def find_amount(t):
    """-> (amount, alt_amount, auto_fixed, raw_amount, has_rupee_sign). Ignores the status bar, promo text, IDs and times;
    the paid amount usually appears twice (next to the merchant and under 'Debited from'), so the most frequent value wins."""
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    i0 = next((i for i, l in enumerate(lines) if re.search(r"success|paid to|payment|sent to|received|credited|completed|paying", l, re.I)), None)
    lines = lines[i0:] if i0 is not None else [l for i, l in enumerate(lines) if not (i < 3 and re.fullmatch(r"\d{1,2}[.:]\d{2}", l))]
    vals, sym, anchor = [], set(), None
    for l in lines:
        if re.search(SKIP, l, re.I): continue
        if anchor is None and re.search(r"paid to|payment to|sent to|received from", l, re.I): anchor = len(vals)
        v = [_num(x) for x in re.findall(CUR + r"\s*" + NUM, l, re.I)]; sym.update(v)
        m = None if v else re.fullmatch(ALONE, l)
        if m and not re.fullmatch(r"(19|20)\d\d|([01]?\d|2[0-3])\.[0-5]\d", m[1]): v = [_num(m[1])]
        vals += [x for x in v if 0 < x < 1e6]
    if not vals: return None, None, None, None, False
    top = Counter(vals).most_common(); tied = [v for v, c in top if c == top[0][1]]
    best = tied[0] if len(tied) == 1 else next((v for v in vals[anchor or 0:] if v in tied), tied[0])
    lead, alt = learned_lead(), None
    if best not in sym and best == int(best):
        s = str(int(best))
        if lead and len(s) >= 2 and s[0] == lead and s[1:].strip("0"): return float(s[1:]), None, lead, best, False
        if lead is None and best >= 100 and s[1:].strip("0"): alt = float(s[1:].lstrip("0"))       # unsure: let the user choose
    return best, alt, None, best, best in sym

def parse_text(t):
    got = re.search(r"received from|money received|credited|you received", t, re.I) and not re.search(r"paid to|payment to|sent to", t, re.I)
    mer = re.search(r"(?:received from|paid to|payment to|sent to|\bto\b|\bfrom\b|paid at|merchant)\s*:?\s*([A-Za-z][\w &.'-]{2,40})", t, re.I)
    d = DATE_RE.search(t); amt, alt, fixed, raw, sym = find_amount(t)
    return dict(amount=amt, alt_amount=alt, auto_fixed=fixed, raw_amount=raw, has_symbol=sym, description=mer[1].strip() if mer else "upi payment",
                date=(d and _iso(d[1])) or date.today().isoformat(), source="screenshot", kind="income" if got else "expense")

def _vision(b):
    import anthropic
    r = anthropic.Anthropic().messages.create(model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"), max_tokens=300, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(b).decode()}},
        {"type": "text", "text": 'Payment screenshot. Reply ONLY JSON: {"amount": number, "merchant": str, "date": "YYYY-MM-DD", "received": true|false}'}]}])
    j = json.loads(re.search(r"\{.*\}", r.content[0].text, re.S)[0])
    return dict(amount=float(j["amount"]), description=j.get("merchant", "upi payment"), date=j.get("date") or date.today().isoformat(),
                source="screenshot-vision", kind="income" if j.get("received") else "expense")

def extract(b):
    from PIL import Image
    out, raw, err, toks = dict(amount=None), "", "", []
    try:
        img = Image.open(io.BytesIO(b)).convert("RGB"); toks = tokens(img); raw = "\n".join(t for _, t in toks); out = parse_text(raw)
        if out["raw_amount"] is not None and not out["has_symbol"]:       # settle the rupee sign from the pixels
            for box, text in toks:
                tx = text.strip()
                if re.fullmatch(r"\d[\d,]*(?:\.\d{1,2})?", tx) and _num(tx) == out["raw_amount"]:
                    v, info = rupee_verdict(img, box, tx); out["glyph_info"] = f"{info} -> {v or 'unclear'}"
                    if v == "digit" and len(tx) >= 2 and tx[1:].strip("0,."): out.update(amount=_num(tx[1:]), alt_amount=None, auto_fixed=f"'{tx[0]}' (rupee sign) removed using image")
                    elif v == "dropped": out.update(amount=_num(tx), alt_amount=None, auto_fixed=None)
                    break
    except ImportError: err = "No OCR engine installed. Run: pip install easyocr   (or pytesseract + Tesseract)"
    except Exception as e: err = f"OCR failed: {type(e).__name__}: {e}"
    if (out.get("amount") is None or out.get("description") == "upi payment") and os.getenv("ANTHROPIC_API_KEY"):
        try: out, err = _vision(b), ""
        except Exception as e: err += f" | vision fallback failed: {e}"
    return {"description": "upi payment", "date": date.today().isoformat(), "source": "screenshot", **out, "raw_text": raw, "error": err}