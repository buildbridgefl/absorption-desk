"""
Trade journal with no hand entry.

Two ways in:
  - a screenshot of a Schwab order/fill, read by Claude (needs ANTHROPIC_API_KEY)
  - Schwab's transaction-history CSV export

Every opening fill gets the scanner's context attached at save time: was
there a fire right before entry, its imbalance / vol z, the trend gate,
which other tickers fired on that bar, and any scheduled release nearby.
That is what lets you later ask: do MY trades on fires beat my trades
without them?
"""

import base64
import hashlib
import io
import json
import re

import numpy as np
import pandas as pd
import requests

from core.signal import bar_end

COLUMNS = ["id", "logged_at", "source", "date", "time", "underlying", "contract",
           "action", "qty", "price", "fees", "thesis",
           "ctx_fire", "ctx_bar", "ctx_imbalance", "ctx_vol_z", "ctx_gate",
           "ctx_cluster", "ctx_news"]

OPTION_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\s+([\d.]+)\s+([CP])\b")


def fill_id(r):
    key = "|".join(str(r.get(k, "")) for k in ("date", "time", "contract", "action", "qty", "price"))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def is_open(action):
    a = str(action).lower()
    return "open" in a or a.strip() in ("buy", "bought", "sell short")


def multiplier(contract):
    return 100 if OPTION_RE.search(str(contract)) else 1


def signed_qty(action, qty):
    a = str(action).lower()
    sign = 1 if a.startswith("buy") or a.startswith("bought") else -1
    return sign * float(qty)


# -------------------------------------------------------- Schwab CSV
def _num(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    s = str(x).replace("$", "").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def parse_schwab_csv(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if "Date" in l and "Action" in l), 0)
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    fee_col = next((c for c in df.columns if c.lower().startswith("fees")), None)
    out = []
    for _, r in df.iterrows():
        action = str(r.get("Action", "")).strip()
        a = action.lower()
        if not (a.startswith("buy") or a.startswith("sell")):
            continue           # skips dividends, transfers, interest, etc.
        sym = str(r.get("Symbol", "")).strip()
        date = str(r.get("Date", "")).split(" ")[0]     # "09/23/2026 as of ..." -> first date
        try:
            date = pd.Timestamp(date).strftime("%Y-%m-%d")
        except Exception:
            continue
        row = {"source": "schwab_csv", "date": date, "time": "",
               "underlying": sym.split(" ")[0] if sym else "",
               "contract": sym, "action": action.title(),
               "qty": abs(_num(r.get("Quantity"))), "price": _num(r.get("Price")),
               "fees": abs(_num(r.get(fee_col))) if fee_col else 0.0, "thesis": ""}
        row["id"] = fill_id(row)
        out.append(row)
    return pd.DataFrame(out)


# -------------------------------------------------------- screenshot
PROMPT = """This is a screenshot from a brokerage app (usually Charles Schwab) showing
one or more executed trades or order confirmations. Extract every FILLED trade.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"fills": [{"date": "YYYY-MM-DD or null", "time": "HH:MM in 24h Eastern or null",
"action": "Buy to Open | Sell to Close | Sell to Open | Buy to Close | Buy | Sell",
"underlying": "SPY", "contract": "SPY 09/24/2026 770.00 C (options: underlying MM/DD/YYYY strike C/P; stock: just the ticker)",
"qty": 2, "price": 1.45, "fees": 0}]}

Rules: only use numbers visible in the image; use null when a field is not shown;
ignore cancelled or working (unfilled) orders; price is per share/contract as displayed."""


def read_screenshot(image: bytes, media_type: str, api_key: str,
                    model: str = "claude-sonnet-5") -> list[dict]:
    body = {"model": model, "max_tokens": 1200,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": PROMPT}]}]}
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"API {r.status_code}: {r.text[:200]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    fills = json.loads(text).get("fills", [])
    out = []
    for f in fills:
        out.append({"source": "screenshot", "date": f.get("date") or "",
                    "time": f.get("time") or "",
                    "underlying": (f.get("underlying") or "").upper(),
                    "contract": f.get("contract") or "",
                    "action": f.get("action") or "", "qty": f.get("qty") or 0,
                    "price": f.get("price") or 0.0, "fees": f.get("fees") or 0.0,
                    "thesis": ""})
    return out


# ----------------------------------------------------------- context
def attach_context(row: dict, scans: dict, events: pd.DataFrame) -> dict:
    """scans: ticker -> scanned hourly frame (core.signal.scan output)."""
    row = dict(row)
    for k in COLUMNS:
        row.setdefault(k, "")
    if not is_open(row.get("action")):
        return row
    h = scans.get(str(row.get("underlying", "")).upper())
    if h is None or h.empty or not row.get("date"):
        row["ctx_fire"] = "unknown"
        return row

    day = pd.Timestamp(row["date"])
    if not row.get("time"):                     # CSV has no time: day-level only
        n = int(h[(h.index.normalize() == day) & h.fire].shape[0])
        row["ctx_fire"] = "unknown"
        row["ctx_bar"] = f"{n} fire(s) that day"
        return row

    entry = pd.Timestamp(f"{row['date']} {row['time']}")
    done = h[(h.index.normalize() == day) & (h.bar_end <= entry)]
    if done.empty:
        row["ctx_fire"] = "no"
        row["ctx_bar"] = "before first bar closed"
        return row
    recent = done.tail(2)                       # bar just closed, or the one before
    fired = recent[recent.fire]
    b = fired.iloc[-1] if len(fired) else done.iloc[-1]
    bts = fired.index[-1] if len(fired) else done.index[-1]
    row["ctx_fire"] = "yes" if len(fired) else "no"
    row["ctx_bar"] = f"{bts:%H:%M}"
    row["ctx_imbalance"] = round(float(b.imbalance), 2)
    row["ctx_vol_z"] = round(float(b.vol_z), 2) if pd.notna(b.vol_z) else ""
    row["ctx_gate"] = "up" if bool(b.gate) else "down"
    if len(fired):
        others = [tk for tk, f in scans.items()
                  if tk != row["underlying"] and bts in f.index and bool(f.at[bts, "fire"])]
        row["ctx_cluster"] = " ".join(others)
    if events is not None and len(events):
        near = events[(events.time >= entry - pd.Timedelta(minutes=90))
                      & (events.time <= entry + pd.Timedelta(minutes=30))]
        row["ctx_news"] = "; ".join(f"{t:%H:%M} {e}" for t, e in zip(near.time, near.event))
    return row


# --------------------------------------------------------- scorecard
def positions(j: pd.DataFrame) -> pd.DataFrame:
    """Round trips per contract, oldest fills first. A position is closed
    when its net quantity returns to zero."""
    if j.empty:
        return pd.DataFrame()
    j = j.copy()
    j["qty"] = pd.to_numeric(j.qty, errors="coerce").fillna(0)
    j["price"] = pd.to_numeric(j.price, errors="coerce").fillna(0)
    j["fees"] = pd.to_numeric(j.fees, errors="coerce").fillna(0)
    j = j.sort_values(["date", "time"], na_position="first")
    out = []
    for contract, g in j.groupby("contract", sort=False):
        mult = multiplier(contract)
        net, cash, start, fire = 0.0, 0.0, None, None
        for _, r in g.iterrows():
            q = signed_qty(r.action, r.qty)
            if net == 0:
                start, cash = r, 0.0
                fire = r.get("ctx_fire", "")
            net += q
            cash += -q * r.price * mult - r.fees
            if abs(net) < 1e-9:
                out.append({"contract": contract, "opened": start.date,
                            "fire": fire if fire in ("yes", "no") else "unknown",
                            "pnl": round(cash, 2)})
                net = 0.0
    return pd.DataFrame(out)


def scorecard(pos: pd.DataFrame, min_n=20):
    res = {}
    for side in ("yes", "no"):
        p = pos[pos.fire == side] if len(pos) else pos
        n = len(p)
        res[side] = {"n": n,
                     "avg": p.pnl.mean() if n >= min_n else None,
                     "win": (p.pnl > 0).mean() if n >= min_n else None}
    return res
