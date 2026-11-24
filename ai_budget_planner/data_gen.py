"""Synthetic transaction generator: 3 personas x N users x 24 months, with seasonality,
noisy bank/UPI-style descriptions and injected anomalies (for evaluation)."""
import calendar
import numpy as np, pandas as pd

from core import CATS   # categories are defined once, in core.py
MERCHANTS = {
 "Food": ["swiggy","zomato","dominos","mcdonalds","chai tapri","bigbasket","dmart groceries","blinkit","cafe coffee day","vada pav stall","tea","lunch"],
 "Transport": ["uber","ola","rapido","metro recharge","petrol pump","irctc","msrtc bus","fastag","auto rickshaw"],
 "Rent": ["house rent","pg rent","hostel fees","landlord transfer"],
 "Bills": ["electricity bill mseb","jio recharge","airtel broadband","water bill","gas cylinder","dth recharge"],
 "Shopping": ["amazon","flipkart","myntra","ajio","decathlon","croma","reliance trends"],
 "Entertainment": ["netflix","spotify","bookmyshow","pvr cinemas","steam games","hotstar"],
 "Health": ["apollo pharmacy","medplus","dr clinic","gym membership","lab test"],
 "Education": ["udemy","coursera","college fees","stationery","exam form","books"],
 "Other": ["atm cash","gift","donation","misc"],
}
PERSONAS = {
 "student":  dict(Food=3500,Transport=1200,Rent=5000,Bills=600,Shopping=1500,Entertainment=900,Health=300,Education=1500,Other=400),
 "salaried": dict(Food=7000,Transport=3000,Rent=15000,Bills=2500,Shopping=4000,Entertainment=2500,Health=1000,Education=500,Other=1000),
 "family":   dict(Food=14000,Transport=5000,Rent=18000,Bills=6000,Shopping=6000,Entertainment=2500,Health=3000,Education=5000,Other=2000),
}
NTX = dict(Food=22,Transport=10,Rent=1,Bills=4,Shopping=4,Entertainment=3,Health=2,Education=2,Other=3)
SEASON = {"Shopping":{10:1.5,11:1.4,12:1.2}, "Entertainment":{12:1.4,5:1.2},
          "Education":{6:1.8,7:1.5,1:1.2}, "Bills":{4:1.2,5:1.3,6:1.2}}
TEMPL = ["{m}","upi {m} {r}","paid to {m}","{m} order #{r}","pos {m} blr","{m} payment","UPI/{r}/{m}"]
GENERIC = ["upi payment","paid via gpay","phonepe transfer","payment","upi transfer"]

def _text(m, rng):
    if rng.random() < 0.05: return str(rng.choice(GENERIC)) + f" {rng.integers(1000,99999)}"
    s = str(rng.choice(TEMPL)).format(m=m, r=rng.integers(10000,999999))
    if rng.random() < 0.1 and len(m) > 4:           # typo
        i = rng.integers(1, len(m)-1); s = s.replace(m, m[:i]+m[i+1:], 1)
    return s

def generate(users_per_persona=10, months=24, start="2024-10-01", seed=42):
    rng = np.random.default_rng(seed); rows = []
    periods = pd.period_range(start, periods=months, freq="M")
    for persona, base in PERSONAS.items():
        for k in range(users_per_persona):
            uid = f"{persona}_{k}"; scale = rng.lognormal(0, 0.15)
            for t, p in enumerate(periods):
                for cat, b in base.items():
                    seas = SEASON.get(cat, {}).get(p.month, 1.0)
                    noise = 1.0 if cat == "Rent" else rng.lognormal(0, 0.10)
                    total = b * scale * seas * (1 + 0.004*t) * noise
                    n = max(1, rng.poisson(NTX[cat])); amts = total * rng.dirichlet(np.ones(n)*2)
                    dim = calendar.monthrange(p.year, p.month)[1]
                    days = rng.integers(1, 6 if cat == "Rent" else dim+1, n)
                    for a, d in zip(amts, days):
                        anom = rng.random() < 0.01
                        if anom: a *= rng.uniform(5, 10)
                        rows.append((uid, persona, f"{p.year}-{p.month:02d}-{d:02d}",
                                     _text(rng.choice(MERCHANTS[cat]), rng), round(float(a), 2), cat, int(anom)))
    return pd.DataFrame(rows, columns=["user","persona","date","description","amount","category","is_anomaly"])

if __name__ == "__main__":
    df = generate(); df.to_csv("data/transactions.csv", index=False); print(df.shape); print(df.head())