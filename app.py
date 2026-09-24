"""Absorption Desk — streamlit run app.py"""

import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import data, journal, news
from core import signal as sig
from core.storage import CSVStore

st.set_page_config(page_title="Absorption Desk", page_icon="🔶", layout="wide")

AMBER, UP, DOWN, MUTED = "#F2B544", "#5AA9E6", "#E4763F", "#9AA3AE"
DEFAULT_WATCH = "SPY, QQQ, IWM, SMH, DIA, XLK, XLF, XLE"


def secret(key):
    try:
        v = st.secrets.get(key)
    except Exception:
        v = None
    return v or os.environ.get(key)


# ------------------------------------------------------------ cached data
@st.cache_data(ttl=300, show_spinner=False)
def c_hourly(tk, period="90d"):
    return data.hourly(tk, period)


@st.cache_data(ttl=900, show_spinner=False)
def c_daily(tk):
    return data.daily(tk)


@st.cache_data(ttl=300, show_spinner=False)
def c_bars(tk, tf):
    return data.bars(tk, tf)


@st.cache_data(ttl=1800, show_spinner=False)
def c_calendar():
    try:
        return news.econ_calendar(), None
    except Exception as e:
        return pd.DataFrame(columns=["time", "event", "impact", "fed"]), type(e).__name__


@st.cache_data(ttl=600, show_spinner=False)
def c_headlines_spy():
    return news.headlines_spy()


@st.cache_data(ttl=600, show_spinner=False)
def c_headlines_tk(tks):
    return news.headlines_tickers(list(tks))


@st.cache_data(ttl=43200, show_spinner=False)
def c_earnings():
    return news.heavyweight_earnings()


def scanned(tk, period="90d"):
    h, d = c_hourly(tk, period), c_daily(tk)
    if h.empty or len(h) < sig.VOL_WINDOW + 5 or len(d) < sig.MA_LEN:
        return None
    return sig.scan(h, d)


# ---------------------------------------------------------------- sidebar
st.sidebar.title("Absorption Desk")
watch = [t.strip().upper() for t in st.sidebar.text_area("Watchlist", DEFAULT_WATCH)
         .split(",") if t.strip()]
if st.sidebar.button("Refresh data", width="stretch"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(f"Signal: imbalance < −{sig.IMB_THR}, vol z > {sig.VOLZ_THR}, "
                   "daily close above 200MA. Validated values, fixed on purpose.")
st.sidebar.caption("Yahoo data, ~15 min delayed. Not financial advice.")

scans, errors = {}, {}
with st.spinner("Scanning…"):
    for tk in watch:
        try:
            s = scanned(tk)
            if s is None:
                errors[tk] = "not enough data"
            else:
                scans[tk] = s
        except Exception as e:
            errors[tk] = type(e).__name__
cal, cal_err = c_calendar()
now = sig.now_et()

t_scan, t_chart, t_news, t_log = st.tabs(["Scanner", "Chart", "News", "Journal"])


# ================================================================ SCANNER
with t_scan:
    st.caption(f"{now:%a %b %d · %H:%M} ET")

    ref = scans.get("SPY")
    if ref is None and scans:
        ref = next(iter(scans.values()))
    c1, c2 = st.columns(2)
    if ref is not None:
        last, forming = sig.latest(ref)
        gate_up = bool(last.gate) if last is not None else False
        c1.metric("Daily trend (SPY)", "UP · gate open" if gate_up else "DOWN · gate shut")
        if last is not None:
            c2.metric("Last completed bar",
                      f"{last.name:%H:%M}–{last.bar_end:%H:%M}",
                      f"{last.name:%b %d}", delta_color="off")

    upcoming = cal[cal.time > now] if len(cal) else cal
    if len(upcoming):
        e = upcoming.iloc[0]
        st.info(f"**Next scheduled:** {e.time:%a %H:%M} ET · {e.event} ({e.impact})")

    # which tickers fired on their last completed bar
    fired = []
    for tk, h in scans.items():
        last, _ = sig.latest(h)
        if last is not None and bool(last.fire):
            fired.append((tk, last))
    same_bar = {}
    for tk, last in fired:
        same_bar.setdefault(last.name, []).append(tk)

    st.subheader(f"Fired on the last completed bar · {len(fired)} of {len(scans)}")
    if not fired:
        st.caption("No fires on the most recent completed bar.")
    for tk, last in fired:
        with st.container(border=True):
            a, b, c, d_ = st.columns([1.1, 1, 1, 1.2])
            a.markdown(f"### {tk}")
            b.metric("price", f"{last.Close:.2f}")
            c.metric("imbalance", f"{last.imbalance:.2f}")
            d_.metric("vol z", f"{last.vol_z:.2f}", sig.volz_class(last.vol_z),
                      delta_color="off")
    for bar, tks in same_bar.items():
        if len(tks) >= 5:
            st.warning(f"**Cluster:** {len(tks)} tickers fired on the {bar:%H:%M} bar "
                       f"({', '.join(tks)}). Cluster fires are hypothesis 4 — "
                       "not yet tested against single fires.")

    # rarity
    pick = st.selectbox("How rare is it? — ticker", list(scans), key="rare_tk",
                        index=list(scans).index("SPY") if "SPY" in scans else 0) \
        if scans else None
    if pick:
        h = scans[pick]
        win = h[h.index >= h.index[-1] - pd.Timedelta(days=20)]
        f = win[win.fire]
        stand = f[f.vol_z >= sig.STANDOUT_VOLZ]
        with st.container(border=True):
            r1, r2, r3 = st.columns(3)
            r1.metric("Fires, last 20 days", f"{len(f)} in {len(win)} bars",
                      f"about 1 in {len(win)//max(len(f),1)}" if len(f) else None,
                      delta_color="off")
            r2.metric(f"Standouts (vol z ≥ {sig.STANDOUT_VOLZ:.0f})", len(stand),
                      ", ".join(f"{t:%b %d}" for t in stand.index) or None,
                      delta_color="off")
            last, _ = sig.latest(h)
            r3.metric("Latest fire", f"{f.index[-1]:%b %d %H:%M}" if len(f) else "—",
                      f"vol z {f.vol_z.iloc[-1]:.2f} · {sig.volz_class(f.vol_z.iloc[-1])}"
                      if len(f) else None, delta_color="off")
            st.caption("Validated edge: +0.09% to +0.31% over drift across 440 "
                       "out-of-sample events. A tilt, not a forecast.")

    with st.expander("Forming bar (live, all tickers)"):
        rows = []
        for tk, h in scans.items():
            last, forming = sig.latest(h)
            young = forming is not None and (now - forming.name).total_seconds() < 15 * 60
            rows.append({"ticker": tk,
                         "price": round(float(h.Close.iloc[-1]), 2),
                         "gate": "up" if last is not None and bool(last.gate) else "down",
                         "imbalance (forming)": None if forming is None or young
                         else round(float(forming.imbalance), 2),
                         "vol z (forming)": None if forming is None or young
                         else round(float(forming.vol_z), 2),
                         "last bar": "FIRED" if last is not None and bool(last.fire) else "—"})
        for tk, err in errors.items():
            rows.append({"ticker": tk, "last bar": f"error: {err}"})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("Forming-bar numbers stay blank for 15 minutes and can look nothing "
                   "like the completed bar that fired.")
    st.caption("A monitor, not a trade recommendation. The signal is validated; "
               "a strategy built on it is not.")


# ================================================================== CHART
with t_chart:
    tk = st.selectbox("Ticker", watch, key="chart_tk")
    tf = st.segmented_control("Timeframe", list(data.TIMEFRAMES), default="1h",
                              key="chart_tf") or "1h"
    try:
        b = c_bars(tk, tf)
        hist = scanned(tk, "730d" if tf in ("4h", "D") else "90d")
    except Exception as e:
        b, hist = pd.DataFrame(), None
        st.error(f"Data error: {type(e).__name__}")

    if len(b):
        span = data.TIMEFRAMES[tf][2]
        w = b[b.index >= b.index[-1] - span]
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=w.index, open=w.Open, high=w.High, low=w.Low, close=w.Close, name=tk,
            increasing=dict(line=dict(color=UP), fillcolor=UP),
            decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN)))
        if tf == "D":
            full = c_daily(tk)
            for n, col in [(50, "#C9B38A"), (200, "#8A94A0")]:
                ma = full.Close.rolling(n).mean().reindex(w.index)
                fig.add_trace(go.Scatter(x=w.index, y=ma, name=f"{n}MA",
                                         line=dict(color=col, width=1.2)))
        if hist is not None:
            fire_times = hist.index[hist.fire]
            pts = [p for p in sig.map_to_bars(fire_times, w.index) if p in w.index]
            if pts:
                fig.add_trace(go.Scatter(
                    x=pts, y=w.loc[pts, "Low"] * 0.998, mode="markers",
                    name="hourly fire",
                    marker=dict(symbol="triangle-up", size=12, color=AMBER)))
        if tf not in ("4h", "D") and len(cal):
            ev = cal[(cal.time >= w.index[0]) & (cal.time <= w.index[-1] + pd.Timedelta(hours=1))]
            for t, name in zip(ev.time, ev.event):
                fig.add_vline(x=t, line=dict(color="#8A94A0", dash="dash", width=1))
                fig.add_annotation(x=t, y=1, yref="paper", text=name[:22],
                                   showarrow=False, font=dict(size=10, color=MUTED),
                                   xanchor="right", yanchor="top", textangle=-90)
        breaks = [dict(bounds=["sat", "mon"])]
        if tf not in ("D",):
            breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
        fig.update_xaxes(rangebreaks=breaks)
        fig.update_layout(height=520, template="plotly_dark",
                          paper_bgcolor="#0D1015", plot_bgcolor="#11151B",
                          xaxis_rangeslider_visible=False,
                          margin=dict(l=8, r=8, t=24, b=8),
                          legend=dict(orientation="h", y=1.06))
        st.plotly_chart(fig, width="stretch")
        st.caption("Fires are always computed on the hourly bar, then drawn on the "
                   "bar of this timeframe where that hour closed. Dashed lines are "
                   "scheduled releases (this week only).")

    if hist is not None:
        ft = sig.forward_table(hist)
        if len(ft):
            ft = ft.sort_values("bar", ascending=False).head(15)
            lf = ft.iloc[0]
            with st.container(border=True):
                st.markdown(f"**Latest fire** · {lf.bar:%a %b %d %H:%M}")
                k = st.columns(4)
                k[0].metric("price", f"{lf.price:.2f}")
                k[1].metric("imbalance", f"{lf.imbalance:.2f}")
                k[2].metric("vol z", f"{lf['vol z']:.2f}", sig.volz_class(lf["vol z"]),
                            delta_color="off")
                k[3].metric("+1h", "pending" if pd.isna(lf["+1h"]) else f"{lf['+1h']:+.2f}%")
            show = ft.copy()
            show["bar"] = show.bar.dt.strftime("%b %d %H:%M")
            for c in ("+1h", "+4h", "+8h", "+16h"):
                show[c] = show[c].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
            st.dataframe(show.style.apply(
                lambda r: [f"background-color:#3A3222" if r["vol z"] >= sig.STANDOUT_VOLZ
                           else "" for _ in r], axis=1),
                width="stretch", hide_index=True)
            st.caption("Highlighted = standout (vol z ≥ 4). Raw price moves, no "
                       "costs, no option translation.")


# =================================================================== NEWS
with t_news:
    mode = st.segmented_control("Feed", ["SPY mode · macro", "Watchlist tickers"],
                                default="SPY mode · macro", key="news_mode") \
        or "SPY mode · macro"

    st.subheader("Economic calendar")
    if cal_err:
        st.caption(f"Calendar unavailable right now ({cal_err}).")
    elif not len(cal):
        st.caption("No USD high/medium releases listed this week.")
    else:
        spy = scans.get("SPY")
        fire_bars = spy.index[spy.fire] if spy is not None else []
        for day, g in cal.groupby(cal.time.dt.normalize()):
            st.markdown(f"**{day:%a %b %d}**")
            for _, e in g.iterrows():
                inside = any(fb <= e.time < sig.bar_end(fb) for fb in fire_bars)
                status = "done" if e.time <= now else "upcoming"
                tag = " · 🏦 Fed" if e.fed else ""
                line = f"`{e.time:%H:%M}` {e.event} — {e.impact}{tag} · {status}"
                if inside:
                    st.markdown(f"{line}  \n:orange[↳ inside a SPY fire bar]")
                else:
                    st.markdown(line)

    st.subheader("SPY heavyweights reporting (next 14 days)")
    try:
        er = c_earnings()
        if len(er):
            st.markdown(" · ".join(f"**{r.ticker}** {r.date:%a %b %d}" for r in er.itertuples()))
        else:
            st.caption("None of the tracked heavyweights report in the next 14 days.")
    except Exception as e:
        st.caption(f"Earnings dates unavailable ({type(e).__name__}).")

    st.subheader("Headlines")
    if mode.startswith("SPY"):
        hl, errs = c_headlines_spy()
        note = "Hidden in SPY mode: insider buys, small caps, anything without a macro or heavyweight tie."
    else:
        hl, errs = c_headlines_tk(tuple(watch)), []
        note = "Per-ticker news from Yahoo."
    if len(hl):
        for r in hl.head(25).itertuples():
            when = "" if pd.isna(r.time) else f"{r.time:%b %d %H:%M} · "
            st.markdown(f"[{r.title}]({r.link})  \n"
                        f"<span style='color:{MUTED};font-size:0.85em'>{when}{r.source} · {r.tag}</span>",
                        unsafe_allow_html=True)
    else:
        st.caption("No headlines loaded.")
    if errs:
        st.caption("Feeds that failed: " + ", ".join(errs))
    st.caption(note)


# ================================================================ JOURNAL
def store():
    if "j_store" not in st.session_state:
        token, repo = secret("GITHUB_TOKEN"), secret("DATA_REPO")
        default = "absorption/journal.csv" if (token and repo) \
            else os.path.expanduser("~/.absorption_journal.csv")
        st.session_state.j_store = CSVStore(journal.COLUMNS,
                                            path=secret("JOURNAL_PATH") or default,
                                            token=token, repo=repo,
                                            branch=secret("DATA_BRANCH") or "main")
    return st.session_state.j_store


def load_journal(force=False):
    if force or "journal" not in st.session_state:
        try:
            st.session_state.journal = store().load()
        except Exception as e:
            st.error(f"Could not load journal: {e}")
            st.session_state.journal = pd.DataFrame(columns=journal.COLUMNS)
    return st.session_state.journal


def save_rows(new_rows, msg):
    j = load_journal()
    have = set(j.id.astype(str)) if len(j) else set()
    fresh = []
    for r in new_rows:
        r = journal.attach_context(r, scans, cal)
        r["id"] = r.get("id") or journal.fill_id(r)
        r["logged_at"] = f"{now:%Y-%m-%d %H:%M}"
        if r["id"] not in have:
            fresh.append(r)
    if not fresh:
        st.info("Nothing new — those fills are already in the journal.")
        return
    try:
        st.session_state.journal = store().save(
            pd.concat([j, pd.DataFrame(fresh)], ignore_index=True), msg)
    except Exception as e:
        st.error(f"Save failed: {e}")
        return
    st.session_state.pop("shot_fills", None)
    st.success(f"Saved {len(fresh)} fill(s).")
    st.rerun()


with t_log:
    j = load_journal()
    st.caption(f"Stored in {store().label}")
    left, right = st.columns(2)

    with left:
        st.markdown("**Snap a Schwab screenshot**")
        shot = st.file_uploader("Screenshot", type=["png", "jpg", "jpeg", "webp"],
                                label_visibility="collapsed", key="shot")
        key = secret("ANTHROPIC_API_KEY")
        if shot and not key:
            st.warning("Add ANTHROPIC_API_KEY to the app's secrets to read screenshots.")
        if shot and key and st.button("Read it", type="primary", width="stretch"):
            with st.spinner("Reading the screenshot…"):
                try:
                    st.session_state.shot_fills = journal.read_screenshot(
                        shot.getvalue(), shot.type or "image/png", key,
                        secret("CLAUDE_MODEL") or "claude-sonnet-5")
                except Exception as e:
                    st.error(f"Couldn't read it: {e}")

    with right:
        st.markdown("**Import weekly Schwab CSV**")
        up = st.file_uploader("Schwab transactions CSV", type=["csv"],
                              label_visibility="collapsed", key="csv")
        if up:
            try:
                parsed = journal.parse_schwab_csv(up.getvalue())
                have = set(j.id.astype(str)) if len(j) else set()
                new = parsed[~parsed.id.isin(have)] if len(parsed) else parsed
                st.caption(f"{len(parsed)} trades in file · {len(new)} new")
                if len(new) and st.button(f"Import {len(new)} trades", width="stretch"):
                    save_rows(new.to_dict("records"), "import schwab csv")
            except Exception as e:
                st.error(f"Couldn't parse that CSV: {e}")
            st.caption("Schwab's history has dates but no times, so CSV trades get "
                       "day-level context only. Screenshots get the exact bar.")

    fills = st.session_state.get("shot_fills")
    if fills is not None:
        with st.container(border=True):
            if not fills:
                st.warning("No filled trades found in that screenshot.")
            else:
                st.markdown("**Read from screenshot — check it, fix anything, then save**")
                ed = st.data_editor(pd.DataFrame(fills)[
                    ["date", "time", "underlying", "contract", "action", "qty", "price", "fees"]],
                    num_rows="dynamic", width="stretch", key="shot_edit")
                thesis = st.text_input("Thesis, one line (optional)",
                                       placeholder="Why this trade, before you know the outcome")
                if st.button("Save trade", type="primary"):
                    rows = ed.to_dict("records")
                    for r in rows:
                        r.update(source="screenshot", thesis=thesis,
                                 underlying=str(r.get("underlying", "")).upper())
                    save_rows(rows, "log from screenshot")

    # scorecard
    pos = journal.positions(j) if len(j) else pd.DataFrame()
    sc = journal.scorecard(pos) if len(pos) else {"yes": {"n": 0}, "no": {"n": 0}}
    st.subheader("Your trades: on a fire vs. without")
    a, b_ = st.columns(2)
    for col, side, label in [(a, "yes", "On a fire"), (b_, "no", "Without a fire")]:
        s = sc[side]
        with col.container(border=True):
            st.markdown(f"**{label}**")
            if s.get("avg") is None:
                st.metric("avg P&L per trade", "—", f"{s['n']} closed trades", delta_color="off")
            else:
                st.metric("avg P&L per trade", f"${s['avg']:,.0f}",
                          f"{s['n']} trades · {s['win']:.0%} wins", delta_color="off")
    st.caption("Averages appear once each side has 20+ closed trades. Before that it's noise.")

    if len(j):
        with st.expander(f"Journal · {len(j)} fills (edit or delete rows here)"):
            edited = st.data_editor(j, num_rows="dynamic", width="stretch",
                                    key="j_edit")
            c1, c2 = st.columns(2)
            if c1.button("Save edits"):
                try:
                    st.session_state.journal = store().save(edited, "edit journal")
                    st.session_state.pop("j_edit", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")
            if c2.button("Reload"):
                load_journal(force=True)
                st.rerun()
            st.download_button("Download CSV", j.to_csv(index=False), "absorption_journal.csv")
