"""Payment screenshot -> amount, merchant, date, paid/received. OCR + regex; the rupee sign is settled by looking at the pixels."""
import base64, io, json, os, re, threading
from collections import Counter
from datetime import date
from pathlib import Path
import numpy as np
from importer import DATE_RE, _iso

CAL = Path(__file__).parent / "data" / "ocr_calib.json"   # remembers how this phone/OCR engine renders the rupee sign
CUR, NUM = r"(?:₹|rs\.?|inr|[?%])", r"(\d[\d,]*(?:\.\d{1,2})?)"
SKIP = r"up\s*to|limit|cashback|offer|reward|balance|secure|utr|\bid\b|ref|\d{1,2}:\d{2}"           # promo text, IDs, times
ALONE = r"[^\w\s]?\s*[zZtT]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}(?:\.\d{1,2})?)\s*[^\w\s]?"   # a line that is just a number
_lock = threading.Lock()      # one OCR job at a time, so two uploads can't double the memory use
_num = lambda s: float(s.replace(",", ""))

def tokens(img):
    """-> [(box, text)] with box = 4 corner points. Engine: RapidOCR (light, pip-only: best for small servers like Render's free plan),
    else EasyOCR (heavy: loads PyTorch, ~1 GB RAM), else Tesseract (needs the Tesseract program installed)."""
    global _R
    try:
        from rapidocr_onnxruntime import RapidOCR
        _R = globals().get("_R") or RapidOCR(); res, _ = _R(np.array(img)); return [(b, t) for b, t, _ in res or []]
    except ImportError: pass
    try:
        import easyocr
        _R = globals().get("_R") or easyocr.Reader(["en"], gpu=False); return [(b, t) for b, t, _ in _R.readtext(np.array(img))]
    except ImportError: pass
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
    t = re.sub(r"(?<=\d )0ct\b", "Oct", t)      # OCR often reads the letter O as a zero
    got = re.search(r"received from|money received|credited|you received", t, re.I) and not re.search(r"paid to|payment to|sent to", t, re.I)
    mer = re.search(r"(?:received from|paid to|payment to|sent to|\bto\b|\bfrom\b|paid at|merchant)\s*:?\s*([A-Za-z][\w &.'-]{2,40})", t, re.I)
    d = DATE_RE.search(t); amt, alt, fixed, raw, sym = find_amount(t)
    return dict(amount=amt, alt_amount=alt, auto_fixed=fixed, raw_amount=raw, has_symbol=sym, description=re.sub(r"(\s+[₹\d,.]+)+$", "", mer[1].strip()) if mer else "upi payment",
                date=(d and _iso(d[1])) or date.today().isoformat(), source="screenshot", kind="income" if got else "expense")


def _vision(b):
    import urllib.request
    import urllib.error
    from PIL import Image, ImageOps

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing in Render")

    # Prepare a smaller image for Gemini
    img = Image.open(io.BytesIO(b))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1400, 1400))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")

    prompt = """
    Read this payment screenshot.
    Return ONLY JSON in this exact format:
    {
      "amount": 250,
      "merchant": "Merchant name",
      "date": "2026-11-26",
      "received": false
    }

    amount must be the actual payment amount.
    merchant is the sender or receiver name if visible.
    date must be YYYY-MM-DD.
    received=true means money came into the user's account.
    received=false means the user paid money.
    Do not guess. If the amount is not visible, use null.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": image_data
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0
        }
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash-lite:generateContent"
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error {e.code}: {details[:400]}")

    text = result["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text)

    amount = data.get("amount")
    if amount is None or float(amount) <= 0:
        raise ValueError("Gemini could not identify a valid payment amount")

    return {
        "amount": float(amount),
        "description": data.get("merchant") or "upi payment",
        "date": data.get("date") or date.today().isoformat(),
        "source": "screenshot-gemini",
        "kind": "income" if data.get("received") is True else "expense"
    }



def extract(b):
    from PIL import Image

    if os.getenv("OCR_ENGINE", "").lower() == "vision":
        try:
            out = _vision(b)
            return {
                "description": "upi payment",
                "date": date.today().isoformat(),
                "source": "screenshot",
                **out,
                "raw_text": "",
                "error": ""
            }
        except Exception as e:
            return {
                "amount": None,
                "description": "upi payment",
                "date": date.today().isoformat(),
                "source": "screenshot",
                "raw_text": "",
                "error": f"Gemini vision failed: {e}"
            }

    out, raw, err, toks = dict(amount=None), "", "", []

    try:
        img = Image.open(io.BytesIO(b)).convert("RGB")
        img.thumbnail((2000, 2000))
        with _lock:
            toks = tokens(img)

        raw = "\n".join(t for _, t in toks)
        out = parse_text(raw)

        if out["raw_amount"] is not None and not out["has_symbol"]:
            for box, text in toks:
                tx = text.strip()
                if re.fullmatch(r"\d[\d,]*(?:\.\d{1,2})?", tx) and _num(tx) == out["raw_amount"]:
                    v, info = rupee_verdict(img, box, tx)
                    out["glyph_info"] = f"{info} -> {v or 'unclear'}"
                    if v == "digit" and len(tx) >= 2 and tx[1:].strip("0,."):
                        out.update(amount=_num(tx[1:]), alt_amount=None,
                                   auto_fixed=f"'{tx[0]}' (rupee sign) removed using image")
                    elif v == "dropped":
                        out.update(amount=_num(tx), alt_amount=None, auto_fixed=None)
                    break

    except Exception as e:
        err = f"OCR failed: {type(e).__name__}: {e}"

    if (out.get("amount") is None or out.get("description") == "upi payment") and os.getenv("GEMINI_API_KEY"):
        try:
            out, err = _vision(b), ""
        except Exception as e:
            err += f" | Gemini fallback failed: {e}"

    return {
        "description": "upi payment",
        "date": date.today().isoformat(),
        "source": "screenshot",
        **out,
        "raw_text": raw,
        "error": err
    }
