from __future__ import annotations

import argparse, html, json, re, webbrowser
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from getpass import getpass
from pathlib import Path
from typing import Any

import httpx

from .client import Cineworld
from .exceptions import CineworldError, NotAuthenticatedError

UNLIMITED_URL = "https://www.cineworld.co.uk/static/en/uk/unlimited"
GROUPS_URL = "https://www.cineworld.co.uk/static/en/uk/unlimited-membership-group-2"
STUDENT_URL = "https://www.cineworld.co.uk/static/en/uk/unlimited/uni-days-student-offer"
GROUP_PRICES = {1: 12.99, 2: 17.99, 3: 19.99, 4: 22.99}
BASE_2D = 4.99
PREMIUM = {"Standard 2D": 0, "ScreenX": 0, "Recliner": 3, "Superscreen": 3, "IMAX": 3, "Standard 3D": 2, "IMAX 3D": 5, "4DX": 6, "4DX 3D": 8}
UPLIFT = {"Standard 2D": 0, "ScreenX": 0, "Recliner": 3, "Superscreen": 0, "IMAX": 3, "Standard 3D": 2, "IMAX 3D": 5, "4DX": 6, "4DX 3D": 8}


@dataclass(frozen=True)
class Visit:
    title: str; code: str; showtime: datetime; cinema: str; fmt: str; retail: float; uplift: float


def _env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1); v = v.strip()
                try: v = json.loads(v) if v.startswith('"') else v.strip("'")
                except Exception: pass
                out[k.strip()] = str(v)
    return out


def _save_env(path: Path, values: dict[str, str]) -> None:
    current = _env(path); current.update(values); path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Local secrets/settings. Never commit this file."] + [f"{k}={json.dumps(v)}" for k, v in current.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _walk(x: Any):
    if isinstance(x, dict):
        for k, v in x.items():
            yield str(k), v; yield from _walk(v)
    elif isinstance(x, list):
        for v in x: yield from _walk(v)


def _find(data: Any, *keys: str) -> Any:
    wanted = {re.sub(r"\W", "", k).lower() for k in keys}
    for k, v in _walk(data):
        if re.sub(r"\W", "", k).lower() in wanted and v not in (None, "", [], {}): return v
    return None


def _money_from(data: Any, *keys: str) -> float | None:
    value = _find(data, *keys)
    try:
        n = float(str(value).replace("£", "").replace(",", "")); return n if 0 < n < 1000 else None
    except Exception: return None


def _date_from(value: Any) -> date | None:
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        try: return date.fromisoformat(str(value)[:10])
        except Exception: return None


def _text(url: str) -> str:
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not r.is_success: return ""
        s = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", r.text)
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", s)))
    except Exception: return ""


def _official_prices() -> dict[int, float]:
    text = _text(UNLIMITED_URL); prices = dict(GROUP_PRICES)
    for g in range(1, 5):
        m = re.search(rf"Group\s*{g}[^£]{{0,100}}£\s*(\d+(?:\.\d+)?)\s*a\s*month", text, re.I)
        if m: prices[g] = float(m.group(1))
    return prices


def _group(cinema: str | None, member: Any) -> int | None:
    for _, v in _walk(member):
        if isinstance(v, str):
            m = re.search(r"\bgroup\s*([1-4])\b", v, re.I)
            if m: return int(m.group(1))
    if not cinema: return None
    text = _text(GROUPS_URL)
    pos = [(g, text.lower().find(f"group {g}")) for g in range(1, 5)]
    pos = [(g, p) for g, p in pos if p >= 0]; pos.sort(key=lambda x: x[1])
    for i, (g, start) in enumerate(pos):
        end = pos[i + 1][1] if i + 1 < len(pos) else len(text)
        if cinema.lower() in text[start:end].lower(): return g
    return None


def _student_discount() -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*off", _text(STUDENT_URL), re.I)
    return float(m.group(1)) if m else 25.0


def _format(item: dict) -> str:
    codes = {str(a.get("acronym", "")).upper() for a in item.get("AttributeDescriptions", []) if isinstance(a, dict)}
    codes |= {str(x).upper() for x in (item.get("MyMovie") or {}).get("Attributes", [])}
    d3 = "3D" in codes
    if "4DX" in codes: return "4DX 3D" if d3 else "4DX"
    if "IMAX" in codes: return "IMAX 3D" if d3 else "IMAX"
    if "SS" in codes or "SUPERSCREEN" in codes: return "Superscreen"
    if "REC" in codes or "RECLINER" in codes: return "Recliner"
    if "SCREENX" in codes or "SCX" in codes: return "ScreenX"
    return "Standard 3D" if d3 else "Standard 2D"


def _visits(orders: Any, base: float, start: date) -> list[Visit]:
    now = datetime.now(); seen = {}; orders = orders if isinstance(orders, list) else []
    for item in orders:
        t = item.get("MyTransaction") or {}; movie = item.get("MyMovie") or {}; theatre = item.get("MyTheatre") or {}
        if str(t.get("TransactionType", "")).lower() != "ticket" or t.get("Refunded"): continue
        try: show = datetime.fromisoformat(str(t.get("Showtime")).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception: continue
        if show > now: continue
        fmt = _format(item); code = str(movie.get("MasterMovieCode") or movie.get("FilmCode") or t.get("HOFilmCode") or "unknown")
        cinema = str(theatre.get("TheatreMarketingName") or theatre.get("Name") or t.get("TheatreCode") or "Unknown")
        retail = round(base + PREMIUM.get(fmt, 0), 2); uplift = float(UPLIFT.get(fmt, 0))
        if fmt == "Standard 3D" and (show.date() - start).days >= 90: uplift = 0
        key = (t.get("TheatreCode"), t.get("SessionId"), show.isoformat(), code)
        seen[key] = Visit(str(movie.get("Title") or code), code, show, cinema, fmt, retail, uplift)
    return sorted(seen.values(), key=lambda v: v.showtime)


def _active_unlimited_subscription(member: Any) -> dict[str, Any]:
    """Return the active Unlimited subscription exposed by Cineworld, if present."""
    if not isinstance(member, dict):
        return {}
    subscriber = member.get("subscriberMember")
    if not isinstance(subscriber, dict):
        return {}
    subscriptions = subscriber.get("MemberSubscriptions")
    if not isinstance(subscriptions, list):
        return {}
    unlimited = [
        sub for sub in subscriptions
        if isinstance(sub, dict)
        and str(sub.get("SubscriptionType", "")).lower() == "unlimited"
    ]
    active = [sub for sub in unlimited if str(sub.get("Status", "")).lower() == "active"]
    return (active or unlimited or [{}])[0]


def _subscription_group(subscription: dict[str, Any]) -> int | None:
    for value in (
        subscription.get("PlanType"),
        subscription.get("Group"),
        subscription.get("SubscriptionGroup"),
    ):
        if value is None:
            continue
        match = re.search(r"(?:grp|group)\s*([1-5])", str(value), re.I)
        if match:
            return int(match.group(1))
    return None


def _member_info(member: Any, transactions: Any, orders: Any, env: dict[str, str], cli_cost: float | None, cli_start: date | None):
    cinema_names = [str((x.get("MyTheatre") or {}).get("TheatreMarketingName") or (x.get("MyTheatre") or {}).get("Name")) for x in orders if isinstance(x, dict)]
    cinema_names = [x for x in cinema_names if x and x != "None"]
    cinema = Counter(cinema_names).most_common(1)[0][0] if cinema_names else None

    subscription = _active_unlimited_subscription(member)
    group = _subscription_group(subscription) or _group(cinema, member)
    prices = _official_prices()

    subscription_full = _money_from(subscription, "Cost")
    full = subscription_full or prices.get(group or 1)

    start = (
        cli_start
        or _date_from(env.get("CINEWORLD_MEMBERSHIP_START"))
        or _date_from(subscription.get("SubscribeDate"))
        or _date_from(_find(member, "MembershipStartDate", "MemberSince", "JoinDate", "StartDate", "IssueDate"))
    )

    # Prefer the active subscription's next actual debit because it reflects
    # account-specific discounts/promotions. Fall back to generic profile or
    # public pricing only if Cineworld does not expose it.
    next_payment = _money_from(subscription, "NextPaymentAmount")
    account_cost = next_payment or _money_from(
        member,
        "MonthlyMembershipFee",
        "MonthlyFee",
        "MonthlyPrice",
        "SubscriptionFee",
        "MembershipFee",
        "RecurringAmount",
    )
    if account_cost is None:
        account_cost = _money_from(transactions, "MonthlyMembershipFee", "MonthlyFee", "RecurringAmount")

    env_cost = None
    try:
        env_cost = float(env["CINEWORLD_MONTHLY_COST"]) if env.get("CINEWORLD_MONTHLY_COST") else None
    except Exception:
        pass

    cost = cli_cost or env_cost or account_cost or full or 12.99
    disc = _student_discount()
    tier = "Black / Premium" if start and (date.today() - start).days >= 90 else "Red / early membership"

    subscriber = member.get("subscriberMember") if isinstance(member, dict) else {}
    subscriber = subscriber if isinstance(subscriber, dict) else {}
    return {
        "cinema": cinema,
        "group": group,
        "full": full,
        "cost": cost,
        "start": start,
        "account_cost": account_cost,
        "discount": disc,
        "tier": tier,
        "subscription_status": subscription.get("Status"),
        "plan_type": subscription.get("PlanType"),
        "next_due": _date_from(subscription.get("NextDueDate")),
        "renewal_date": _date_from(subscription.get("RenewalDate")),
        "next_payment": next_payment,
        "payment_past_due": bool(subscriber.get("PaymentPastDue")),
        "source": "Cineworld account" if subscription else "Cineworld public pricing / fallback",
    }


def _login_orders(cw: Cineworld, env_path: Path, save: bool) -> Any:
    env = _env(env_path)
    email = env.get("CINEWORLD_EMAIL")
    password = env.get("CINEWORLD_PASSWORD")

    try:
        orders = cw.get_orders()
        # A persistent browser session may still be valid on the first report
        # run. If the user asked us to remember credentials, collect them once
        # now so a future expired session can be filled automatically.
        if save and (not email or not password):
            email = email or input("Cineworld email (saved locally in .env): ").strip()
            password = password or getpass("Cineworld password (saved locally in .env): ")
            _save_env(env_path, {"CINEWORLD_EMAIL": email, "CINEWORLD_PASSWORD": password})
            print(f"Saved login locally to {env_path.resolve()} (plaintext, gitignored).")
        return orders
    except (NotAuthenticatedError, CineworldError):
        pass

    email = email or input("Cineworld email: ").strip()
    password = password or getpass("Cineworld password: ")
    if save:
        _save_env(env_path, {"CINEWORLD_EMAIL": email, "CINEWORLD_PASSWORD": password})
        print(f"Saved login locally to {env_path.resolve()} (plaintext, gitignored).")
    cw.login(email=email, password=password, auto_submit=False)
    return cw.get_orders()


def _poster(item: dict) -> str:
    media = (item.get("MyMovie") or {}).get("Media") or []
    for kind in ("Mobile_MovieThumbnail", "TV_SmallPosterImage", "TV_TopShelfPosterImage"):
        for m in media:
            if isinstance(m, dict) and m.get("SubType") == kind: return str(m.get("SecureUrl") or m.get("Url") or "")
    return ""


def _html_report(visits: list[Visit], info: dict, membership_start: date, monthly: float, base: float, orders: Any) -> str:
    sub = [v for v in visits if v.showtime.date() >= membership_start]; months = max(1, (date.today().year-membership_start.year)*12 + date.today().month-membership_start.month + 1)
    retail = sum(v.retail for v in sub); uplifts = sum(v.uplift for v in sub); paid = monthly * months; saving = retail - paid - uplifts
    posters = {}
    for x in orders:
        if isinstance(x, dict):
            m=x.get("MyMovie") or {}; posters[str(m.get("MasterMovieCode") or m.get("FilmCode") or "")]=_poster(x)
    fmt = Counter(v.fmt for v in sub); unique = len({v.code for v in visits})
    cards="".join(f'<article><img src="{html.escape(posters.get(v.code,""))}" onerror="this.style.display=\'none\'"><div><b>{html.escape(v.title)}</b><small>{v.showtime:%d %b %Y · %H:%M} · {html.escape(v.cinema)}</small><span>{html.escape(v.fmt)} · £{v.retail:.2f} retail · £{v.uplift:.2f} uplift</span></div></article>' for v in reversed(sub))
    bars="".join(f'<p>{html.escape(k)} <progress value="{n}" max="{max(fmt.values(),default=1)}"></progress> {n}</p>' for k,n in fmt.most_common())
    colour="#45df91" if saving >= 0 else "#ff6d82"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Cineworld Unlimited Report</title><style>
body{{margin:0;background:#080b10;color:#f5f7fa;font-family:system-ui}}main{{max-width:1100px;margin:auto;padding:50px 18px}}h1{{font-size:clamp(44px,8vw,82px);line-height:.95;margin:8px 0 35px}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}section,.card,article{{background:#121821;border:1px solid #29323e;border-radius:18px}}.card,section{{padding:20px}}.big{{font-size:34px;font-weight:800}}small,.muted{{display:block;color:#98a2b3}}section{{margin-top:12px}}.meta{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.meta div{{background:#181f29;padding:13px;border-radius:12px}}article{{display:flex;overflow:hidden}}article img{{width:90px;object-fit:cover}}article div{{padding:14px}}article span{{display:block;color:#98a2b3;font-size:12px;margin-top:10px}}.films{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}progress{{width:65%}}@media(max-width:800px){{.grid,.meta{{grid-template-columns:1fr 1fr}}.films{{grid-template-columns:1fr}}}}@media(max-width:500px){{.grid,.meta{{grid-template-columns:1fr}}}}</style></head><body><main>
<div class="muted">CINEWORLD UNLIMITED</div><h1>Your cinema<br>in numbers.</h1><div class="grid">
<div class="card"><small>Screenings attended</small><div class="big">{len(visits)}</div></div><div class="card"><small>Unique films</small><div class="big">{unique}</div></div><div class="card"><small>Retail value</small><div class="big">£{retail:.2f}</div></div><div class="card"><small>Estimated saved</small><div class="big" style="color:{colour}">£{abs(saving):.2f}</div></div></div>
<section><h2>Membership detected automatically</h2><div class="meta"><div><small>Home cinema</small><b>{html.escape(str(info['cinema'] or 'Unknown'))}</b></div><div><small>Unlimited group</small><b>{'Group '+str(info['group']) if info['group'] else 'Unknown'}</b></div><div><small>Plan</small><b>{html.escape(str(info.get('plan_type') or 'Unlimited'))}</b></div><div><small>Status</small><b>{html.escape(str(info.get('subscription_status') or 'Unknown'))}</b></div><div><small>Full plan price</small><b>£{(info['full'] or monthly):.2f}/mo</b></div><div><small>Current payment used</small><b>£{monthly:.2f}/mo</b></div><div><small>Next payment</small><b>{('£%.2f' % info['next_payment']) if info.get('next_payment') else 'Not exposed'}</b></div><div><small>Next due</small><b>{info.get('next_due') or 'Not exposed'}</b></div><div><small>Membership start</small><b>{membership_start}</b></div><div><small>Card stage</small><b>{info['tier']}</b></div><div><small>Student offer now</small><b>{info['discount']:.0f}% off eligible intro period</b></div><div><small>Standard 2D baseline</small><b>£{base:.2f}</b></div></div></section>
<section><h2>Value calculation</h2><p>Equivalent tickets: <b>£{retail:.2f}</b> · membership ({months} month(s)): <b>£{paid:.2f}</b> · estimated format uplifts: <b>£{uplifts:.2f}</b> · net: <b style="color:{colour}">£{saving:.2f}</b></p><small>Uses account data first, Cineworld's current public Unlimited pages second, and clearly-labelled estimates only where exact historical prices are not exposed.</small></section>
<section><h2>Formats</h2>{bars}</section><section><h2>Your visits</h2><div class="films">{cards}</div></section>
<p class="muted">Generated {datetime.now():%d %b %Y %H:%M}. This HTML contains no Cineworld email, password, cookies or session token.</p></main></body></html>'''


def build_report(output: Path, env_path: Path, monthly_cost=None, start_date=None, standard_price=None, save_credentials=True, open_browser=True):
    env = _env(env_path)
    try: base = float(standard_price or env.get("CINEWORLD_STANDARD_2D_PRICE") or BASE_2D)
    except Exception: base = BASE_2D
    with Cineworld() as cw:
        orders = _login_orders(cw, env_path, save_credentials); member = {} ; tx = []
        try: member = cw.get_member()
        except Exception: pass
        try: tx = cw.get_transactions()
        except Exception: pass
        info = _member_info(member, tx, orders, env, monthly_cost, start_date)
        temp = _visits(orders, base, date.min)
        if not temp: raise RuntimeError("No completed, non-refunded screenings found")
        start = info["start"] or temp[0].showtime.date(); info["start"] = start
        monthly = float(info["cost"]); visits = _visits(orders, base, start)
        output.write_text(_html_report(visits, info, start, monthly, base, orders), encoding="utf-8")
    print(f"Screenings: {len(visits)} | Unique films: {len({v.code for v in visits})} | Group: {info['group'] or 'Unknown'} | £{monthly:.2f}/mo")
    print(f"Report: {output.resolve()}")
    if open_browser: webbrowser.open(output.resolve().as_uri())


def main(argv=None):
    p=argparse.ArgumentParser(description="Generate a local Cineworld Unlimited HTML report")
    p.add_argument("--output",type=Path,default=Path("cineworld_report.html")); p.add_argument("--env-file",type=Path,default=Path(".env")); p.add_argument("--monthly-cost",type=float); p.add_argument("--start-date",type=date.fromisoformat); p.add_argument("--standard-price",type=float); p.add_argument("--no-save-credentials",action="store_true"); p.add_argument("--no-open",action="store_true")
    a=p.parse_args(argv)
    try: build_report(a.output,a.env_file,a.monthly_cost,a.start_date,a.standard_price,not a.no_save_credentials,not a.no_open); return 0
    except KeyboardInterrupt: return 130
    except Exception as e: print(f"Report failed: {e}"); return 1


if __name__ == "__main__": raise SystemExit(main())
