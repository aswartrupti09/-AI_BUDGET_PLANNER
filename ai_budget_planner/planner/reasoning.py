"""Reasoning for a changed budget: what happened, how each TYPE of category is treated (rent is fixed, food is a necessity,
shopping is optional), what was changed and why, what else was tried, checks and advice. Every number comes from the real
computation. explain() turns it into a short AI-written (or built-in) summary."""
import os, re
from functools import lru_cache
import core

_f = lambda v: f"₹{v:,.0f}"
_group = lambda c: next(g for g, cs in core.GROUPS.items() if c in cs)
CUT_WHY = {"Fixed cost": "a fixed cost, so it was barely touched", "Necessity": "a necessity, so it was cut only a little", "Optional": "optional spending, so it takes the biggest cut"}
UP_WHY = {"Fixed cost": "a fixed cost that has to be paid", "Necessity": "a necessity, so it was protected", "Optional": "to match what you really spent"}

def checks_for(plan, budgets, spent, income, goal):
    nb = plan["budgets"]
    return [("Budgets + savings add up to your income", "ok" if abs(sum(nb.values()) + plan["savings"] - income) <= len(nb) + 1 else "fail"),
            ("No category is below what you already spent", "ok" if all(nb[c] + 1 >= spent.get(c, 0) for c in nb) else "fail"),
            ("Fixed costs and necessities stay above their minimum", "ok" if all(nb[c] + 1 >= core.FLOOR_PCT.get(c, 0) * budgets[c] for c in nb) else "fail"),
            (f"Savings goal of {_f(goal)} is met" if plan["shortfall"] == 0 else f"Savings goal not fully met (short by {_f(plan['shortfall'])})", "ok" if plan["shortfall"] == 0 else "warn")]

def build_reasoning(budgets, spent, proj, income, goal, plan):
    cats = list(budgets); over = [c for c in cats if spent.get(c, 0) > budgets[c]]; sav_before = income - sum(budgets.values())
    pts = [f"{c}: you spent {_f(spent[c])} but the budget was {_f(budgets[c])} ({_f(spent[c] - budgets[c])} over)." for c in over] or ["No category is over its budget yet."]
    steps = [("What happened", pts + [f"Income {_f(income)}. Planned savings {_f(sav_before)}, goal {_f(goal)}."])]
    unused = sorted(((c, budgets[c] - proj.get(c, 0)) for c in cats if c not in over and _group(c) != "Fixed cost" and budgets[c] - proj.get(c, 0) >= 50), key=lambda x: -x[1])
    steps.append(("Where spare money can come from", [f"{c}: about {_f(u)} of its budget will probably stay unused." for c, u in unused[:5]] or ["Other categories are expected to use their budgets, so little is free."]))
    g = {k: ", ".join(v) for k, v in core.GROUPS.items()}
    steps.append(("How each type of category is treated", [f"Fixed costs ({g['Fixed cost']}) can't really be reduced, so they are protected.",
        f"Necessities ({g['Necessity']}) are needed, so they are cut only a little and never below a minimum.", f"Optional spending ({g['Optional']}) takes the biggest cut.",
        "Budgets + savings always equal your income, nothing goes below what you already spent, and your savings goal is protected first."]))
    ups, downs = [x for x in plan["changes"] if x["delta"] > 0], [x for x in plan["changes"] if x["delta"] < 0]
    from_surplus = min(sum(x["delta"] for x in ups), max(0, sav_before - goal))
    pts = [f"{x['category']}: {_f(x['old'])} → {_f(x['new'])} (+{_f(x['delta'])}). It is {UP_WHY[_group(x['category'])]}." for x in ups]
    if from_surplus > 0: pts.append(f"{_f(from_surplus)} came from savings above your goal.")
    pts += [f"{x['category']}: {_f(x['old'])} → {_f(x['new'])} (-{_f(-x['delta'])}). It is {CUT_WHY[_group(x['category'])]}." for x in downs]
    pts += [f"{c}: unchanged. It is a fixed cost that can't be reduced." for c in core.GROUPS["Fixed cost"] if plan["changes"] and c in budgets and c not in {x["category"] for x in plan["changes"]}]
    steps.append(("What I changed", pts or ["Nothing needs to change right now."]))
    options = []
    if ups:   # other ways to pay for the overspend
        seen = set()
        for name, pen, desc in [("Protect savings goal", 5.0, "cut categories first"), ("Share the cost", 1.0, "cut categories and dip into savings"), ("Protect categories", 0.1, "use savings first")]:
            p = core.rebalance(budgets, spent, proj, income, goal, pen)
            if (p["savings"], p["shortfall"]) in seen: continue
            seen.add((p["savings"], p["shortfall"]))
            options.append(dict(Option=name, Idea=desc, **{"Savings after": p["savings"], "Short of goal": p["shortfall"], "Cut from categories": -sum(x["delta"] for x in p["changes"] if x["delta"] < 0)}))
        if len(options) > 1:
            steps.append(("Other ways I tested", [f"{o['Option']} ({o['Idea']}): savings {_f(o['Savings after'])}, " + ("goal met" if o["Short of goal"] == 0 else f"{_f(o['Short of goal'])} short of goal") + f", categories cut by {_f(o['Cut from categories'])}." for o in options]))
    if plan["savings"] < 0:
        steps.append(("Why the budget could not be kept", [f"You have already spent {_f(-plan['savings'])} more than your income. No budget can undo money already spent.",
                                                         "So every budget was set to what you spent (or its protected minimum), and optional categories were closed."]))
    elif plan["shortfall"]:
        steps.append(("Why the goal could not be met", [f"Even after cutting optional spending as far as allowed, you are {_f(plan['shortfall'])} short of your {_f(goal)} goal.",
                                                       "Fixed costs and necessities have minimums, so they can't be cut any further."]))
    rec = ("No action needed right now." if not plan["changes"] else
           f"You've spent {_f(-plan['savings'])} more than your income. Add income or spend nothing optional for the rest of the month." if plan["savings"] < 0 else
           "This change covers what you really spent and keeps your savings goal." if not plan["shortfall"] else
           f"Your savings goal can't be fully met: you'd be {_f(plan['shortfall'])} short even after cuts. You could lower the goal by that amount, raise income, or cut more spending.")
    return dict(steps=steps, options=options, checks=checks_for(plan, budgets, spent, income, goal), recommendation=rec)

PROMPT = ("You are a friendly budgeting coach. Explain this budget change to the user in 4 to 6 short bullet points (each starting with '- '), in simple words. "
          "Mention why rent and other fixed costs stay, why necessities like food are protected, and which optional categories were cut. "
          "Use ONLY the facts below. Do not calculate, estimate or add any number that is not written in the facts.\n\nFacts:\n")
@lru_cache(maxsize=64)
def _ask(facts):
    try:
        import anthropic
        text = anthropic.Anthropic().messages.create(model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"), max_tokens=400, messages=[{"role": "user", "content": PROMPT + facts}]).content[0].text.strip()
    except Exception: return ""
    nums = lambda s: {n.replace(",", "") for n in re.findall(r"\d[\d,]*\d", s)}
    return text if nums(text) <= nums(facts) else ""      # reject any answer that invents a number

def explain(r):
    """Plain-language explanation of a changed budget. Written by Claude when ANTHROPIC_API_KEY is set (numbers are verified against
    the computed facts, otherwise it is discarded); else a built-in summary of the same facts. -> dict(text, source)."""
    pts = lambda *titles: [p for t, ps in r["steps"] if t in titles for p in ps]
    facts = "\n".join("- " + p for p in pts("What happened", "How each type of category is treated", "What I changed", "Why the goal could not be met", "Why the budget could not be kept") + [r["recommendation"]])
    if os.getenv("ANTHROPIC_API_KEY") and (text := _ask(facts)): return dict(text=text, source="Claude")
    return dict(text="\n".join("- " + p for p in pts("What I changed", "Why the goal could not be met", "Why the budget could not be kept") + [r["recommendation"]]), source="built-in")