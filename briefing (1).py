#!/usr/bin/env python3
"""
Briefing fondamental trading : daily + weekly, 100 % gratuit, sortie en page web (HTML).

Installation :  pip install requests yfinance feedparser
Usage :
    python briefing.py daily            # briefing du jour  -> briefing_daily.html
    python briefing.py weekly           # récap + semaine à venir -> briefing_weekly.html
    python briefing.py daily --open     # ouvre la page dans le navigateur
    python briefing.py daily --demo     # données factices (test sans internet)
    python briefing.py site             # génère daily + weekly dans ./site (GitHub Actions)

Sources gratuites : calendrier ForexFactory (flux JSON non officiel), Yahoo Finance (yfinance),
flux RSS Fed / BCE / BBC / CNBC / Guardian / France 24.
"""
import argparse, html, json, re, sys, time, webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
CACHE = Path(".cache")
CACHE.mkdir(exist_ok=True)
UA = "Mozilla/5.0 (briefing-perso)"

# NB : l'or est suivi via les futures GC=F (proxy fiable de XAUUSD sur Yahoo)
ASSETS = {"Nasdaq 100": "^NDX", "S&P 500": "^GSPC", "CAC 40": "^FCHI",
          "XAUUSD (or)": "GC=F", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"}
CONTEXT = {"DXY (dollar)": "DX-Y.NYB", "US 10 ans (%)": "^TNX", "VIX": "^VIX", "Pétrole WTI": "CL=F"}
COUNTRIES = {"USD", "EUR", "GBP", "JPY", "CNY"}

FEEDS = {
    "Fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "BCE": "https://www.ecb.europa.eu/rss/press.xml",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "BBC Monde": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNBC Économie": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "Guardian Business": "https://www.theguardian.com/business/rss",
    "France 24": "https://www.france24.com/fr/rss",
}

# thème : (regex, transmission habituelle vers les actifs)
THEMES = {
    "Banques centrales & taux": (r"\bfed\b|powell|\becb\b|lagarde|\bbce\b|\bboe\b|bank of england|rate cut|rate hike|taux directeur|baisse des taux",
                                 "Ton restrictif : USD ↑, XAUUSD ↓, Nasdaq ↓. Ton accommodant : l'inverse."),
    "Inflation & croissance": (r"inflation|\bcpi\b|\bpce\b|recession|récession|\bgdp\b|\bpib\b|jobs report|payroll",
                               "Inflation forte : rendements ↑, actions growth ↓, or ↓. Croissance faible : USD ↓ puis risk-off possible."),
    "Géopolitique / conflits": (r"\bwar\b|ceasefire|missile|ukraine|russia|russie|gaza|israel|iran|taiwan|guerre|cessez-le-feu|nato|otan",
                                "Risk-off typique : XAUUSD ↑, USD ↑ (valeur refuge), indices ↓, pétrole ↑ si Moyen-Orient."),
    "Commerce & sanctions": (r"tariff|droits de douane|trade war|sanction|export control|embargo",
                             "Tensions commerciales : indices ↓ (CAC plus exposé), USD ↑, volatilité ↑."),
    "Énergie": (r"\boil\b|opec|crude|brent|pétrole|\bgas\b|\bgaz\b",
                "Pétrole ↑ : inflation ↑ (pression sur les taux), CAC/DAX plus sensibles."),
    "Grandes entreprises & tech": (r"nvidia|apple|microsoft|alphabet|google|amazon|meta\b|tesla|earnings|résultats trimestriels",
                                   "Les résultats des méga-caps pilotent le Nasdaq et le S&P 500."),
}

RULES = [  # ordre important
    ("banque_centrale", r"fomc|rate decision|interest rate|bank rate|cash rate|refinancing rate|monetary policy|press conference|speaks|statement|minutes|powell|lagarde|bailey"),
    ("inflation", r"cpi|pce|ppi|inflation|price index|core retail"),
    ("emploi", r"non-farm|nfp|employment change|payroll|adp|jobless|claims|unemployment|average hourly|wage"),
    ("croissance", r"gdp|retail sales|pmi|ism|industrial production|confidence|sentiment|durable|housing|manufacturing|services|trade balance"),
]

SCEN = {
    ("USD", "inflation"): ("USD ↑, rendements ↑ → EURUSD ↓, GBPUSD ↓, XAUUSD ↓, Nasdaq/S&P ↓ (taux élevés plus longtemps)",
                           "USD ↓, rendements ↓ → EURUSD ↑, GBPUSD ↑, XAUUSD ↑, Nasdaq/S&P ↑ (espoirs de baisse de taux)"),
    ("USD", "emploi"): ("USD ↑, XAUUSD ↓, indices mitigés (économie solide mais Fed moins accommodante)",
                        "USD ↓, XAUUSD ↑, indices mitigés (craintes de ralentissement vs espoirs de baisse de taux)"),
    ("USD", "croissance"): ("USD ↑, indices stables à ↑, XAUUSD ↓",
                            "USD ↓, XAUUSD ↑, indices ↓ si la déception est forte (peur de ralentissement)"),
    ("USD", "banque_centrale"): ("Ton restrictif : USD ↑, XAUUSD ↓, Nasdaq ↓",
                                 "Ton accommodant : USD ↓, XAUUSD ↑, Nasdaq/S&P ↑"),
}
LABELS = {"banque_centrale": ("Si ton restrictif (hawkish)", "Si ton accommodant (dovish)")}
INVERSE = re.compile(r"unemployment|jobless|claims", re.I)


def classify(title):
    t = title.lower()
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    return None


def scenario(ccy, cat):
    if (ccy, cat) in SCEN:
        return SCEN[(ccy, cat)]
    pair = {"EUR": "EURUSD", "GBP": "GBPUSD"}.get(ccy)
    if pair and cat in ("inflation", "banque_centrale"):
        idx = "CAC 40" if ccy == "EUR" else "indices UK"
        return (f"{ccy} ↑ (banque centrale plus restrictive) → {pair} ↑, {idx} souvent ↓",
                f"{ccy} ↓ → {pair} ↓, {idx} souvent ↑")
    if pair:
        return (f"{ccy} ↑ → {pair} ↑, actions locales stables à ↑",
                f"{ccy} ↓ → {pair} ↓, risque de ralentissement pour les actions locales")
    msg = "Impact indirect via le sentiment de risque mondial (Asie : surveiller le CAC et l'or)."
    return (msg, msg)


# ---------------------------------------------------------------- collecte
def cached_get(url, name, ttl=3600):
    import requests
    f = CACHE / name
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        return f.read_text(encoding="utf-8")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        f.write_text(r.text, encoding="utf-8")
        return r.text
    except Exception:
        if f.exists():  # cache périmé > pas de données
            return f.read_text(encoding="utf-8")
        raise


def load_calendar():
    raw_events = []
    for part in ("thisweek", "nextweek"):
        try:
            raw = cached_get(f"https://nfs.faireconomy.media/ff_calendar_{part}.json", f"cal_{part}.json")
            raw_events += json.loads(raw)
        except Exception as e:
            print(f"[calendrier {part}] indisponible : {e}", file=sys.stderr)
    out = []
    for e in raw_events:
        try:
            dt = datetime.fromisoformat(e["date"]).astimezone(PARIS)
        except Exception:
            continue
        out.append({"dt": dt, "title": e.get("title", ""), "country": e.get("country", ""),
                    "impact": e.get("impact", ""), "forecast": e.get("forecast") or "—",
                    "previous": e.get("previous") or "—"})
    return sorted(out, key=lambda x: x["dt"])


def load_prices():
    import yfinance as yf
    res = {}
    for label, tk in {**ASSETS, **CONTEXT}.items():
        try:
            h = yf.Ticker(tk).history(period="1mo", interval="1d")["Close"].dropna()
            last, prev = float(h.iloc[-1]), float(h.iloc[-2])
            wk = float(h.iloc[-6]) if len(h) >= 6 else None
            if "10 ans" in label:  # rendement : variation en points de base
                res[label] = {"last": last, "d1": (last - prev) * 100, "w": (last - wk) * 100 if wk else None, "unit": "pb"}
            else:
                res[label] = {"last": last, "d1": (last / prev - 1) * 100, "w": (last / wk - 1) * 100 if wk else None, "unit": "%"}
        except Exception as e:
            print(f"[prix {label}] indisponible : {e}", file=sys.stderr)
    return res


def load_news(hours):
    import feedparser
    limit = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for src, url in FEEDS.items():
        try:
            for en in feedparser.parse(url, agent=UA).entries[:40]:
                p = en.get("published_parsed") or en.get("updated_parsed")
                dt = datetime(*p[:6], tzinfo=timezone.utc) if p else datetime.now(timezone.utc)
                if dt >= limit:
                    items.append({"source": src, "title": en.get("title", ""), "link": en.get("link", "#"), "dt": dt})
        except Exception as e:
            print(f"[rss {src}] indisponible : {e}", file=sys.stderr)
    return items


def group_themes(news):
    out = {}
    for theme, (pat, transmission) in THEMES.items():
        hits = [n for n in news if re.search(pat, n["title"], re.I)]
        if hits:
            sources = {n["source"] for n in hits}
            out[theme] = {"transmission": transmission, "sources": sources,
                          "items": sorted(hits, key=lambda n: n["dt"], reverse=True)[:4]}
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1]["sources"])))


def demo():
    day = datetime.now(PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    def ev(h, m, c, t, i, f, p, d=0):
        return {"dt": day + timedelta(days=d, hours=h, minutes=m), "title": t, "country": c, "impact": i, "forecast": f, "previous": p}
    cal = [ev(10, 0, "EUR", "German Final CPI m/m", "Medium", "0.1%", "0.1%"),
           ev(14, 30, "USD", "Core CPI m/m", "High", "0.3%", "0.3%"),
           ev(14, 30, "USD", "Unemployment Claims", "Medium", "230K", "228K"),
           ev(20, 0, "USD", "FOMC Member Speaks", "Medium", "—", "—"),
           ev(9, 30, "GBP", "Official Bank Rate", "High", "4.00%", "4.00%", d=2),
           ev(14, 15, "EUR", "Main Refinancing Rate", "High", "2.15%", "2.15%", d=3)]
    prices = {k: {"last": 100 + i * 37.3, "d1": (-1) ** i * 0.4 * (i + 1) / 2, "w": (-1) ** (i + 1) * 0.9, "unit": "%"}
              for i, k in enumerate({**ASSETS, **CONTEXT})}
    now = datetime.now(timezone.utc)
    news = [{"source": s, "title": t, "link": "#", "dt": now} for s, t in [
        ("Fed", "Powell signals patience on rate cut path"), ("BBC Business", "Fed rate cut hopes fade after inflation data"),
        ("CNBC Économie", "Treasury yields climb as CPI looms"), ("BBC Monde", "Ceasefire talks stall as missile strikes continue"),
        ("France 24", "Guerre en Ukraine : nouvelles sanctions annoncées"), ("Guardian Business", "Oil jumps on OPEC supply worries")]]
    return cal, prices, news


# ---------------------------------------------------------------- rendu
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#14171c;--mut:#6a7280;--bd:#e2e5ea;--up:#0a7d3b;--dn:#c0302b;--hi:#c0302b;--md:#c77d0a;--lo:#6a7280}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--tx:#e8eaee;--mut:#8b93a1;--bd:#262b34;--up:#3ecf7a;--dn:#ff6b64;--hi:#ff6b64;--md:#f0a94a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:920px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0}h2{font-size:17px;margin:32px 0 10px}
.sub{color:var(--mut);margin:4px 0 0}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin:8px 0}
table{width:100%;border-collapse:collapse}td,th{padding:7px 6px;border-bottom:1px solid var(--bd);text-align:right}th{color:var(--mut);font-weight:500;font-size:13px}
td:first-child,th:first-child{text-align:left}.wrap{overflow-x:auto}.up{color:var(--up)}.dn{color:var(--dn)}
.ev{display:flex;gap:12px}.time{font-weight:600;min-width:52px}.badge{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid;margin-left:6px}
.High{color:var(--hi)}.Medium{color:var(--md)}.Low{color:var(--lo)}.meta{color:var(--mut);font-size:13px}
.sc{font-size:13.5px;margin-top:6px}.sc b{font-weight:600}a{color:inherit}li{margin:4px 0}.note{color:var(--mut);font-size:12.5px;margin-top:30px}
"""


def pct(v, unit="%"):
    if v is None:
        return "—"
    cls = "up" if v >= 0 else "dn"
    return f'<span class="{cls}">{v:+.2f}{unit}</span>'


def fmt_price(label, v):
    return f"{v:,.4f}" if "USD" in label and "XAU" not in label and "DXY" not in label else f"{v:,.2f}"


def render_prices(prices):
    def rows(d):
        return "".join(f"<tr><td>{html.escape(k)}</td><td>{fmt_price(k, prices[k]['last'])}</td>"
                       f"<td>{pct(prices[k]['d1'], prices[k]['unit'])}</td><td>{pct(prices[k]['w'], prices[k]['unit'])}</td></tr>"
                       for k in d if k in prices)
    head = "<tr><th>Actif</th><th>Dernier</th><th>1 jour</th><th>5 jours</th></tr>"
    return (f'<div class="card wrap"><table>{head}{rows(ASSETS)}</table></div>'
            f'<h2>Contexte (dollar, taux, volatilité)</h2><div class="card wrap"><table>{head}{rows(CONTEXT)}</table></div>')


def render_event(e):
    cat, ccy = classify(e["title"]), e["country"]
    sc = ""
    if cat:
        hot, cold = scenario(ccy, cat)
        l1, l2 = LABELS.get(cat, ("Si au-dessus du consensus", "Si en dessous du consensus"))
        if INVERSE.search(e["title"]):
            l1, l2 = "Si chiffre élevé (marché du travail plus faible)", "Si chiffre bas (marché du travail plus solide)"
            hot, cold = cold, hot
        sc = f'<div class="sc"><b>{l1} :</b> {html.escape(hot)}<br><b>{l2} :</b> {html.escape(cold)}</div>'
    return (f'<div class="card ev"><div class="time">{e["dt"]:%H:%M}</div><div style="flex:1">'
            f'<b>{html.escape(ccy)}</b> {html.escape(e["title"])}<span class="badge {e["impact"]}">{html.escape(e["impact"])}</span>'
            f'<div class="meta">Prévision : {html.escape(e["forecast"])} · Précédent : {html.escape(e["previous"])}</div>{sc}</div></div>')


def render_agenda(events, by_day):
    if not events:
        return '<div class="card meta">Aucune annonce importante dans cette fenêtre (ou calendrier indisponible).</div>'
    if not by_day:
        return "".join(render_event(e) for e in events)
    out, cur = [], None
    for e in events:
        if e["dt"].date() != cur:
            cur = e["dt"].date()
            out.append(f'<h2>{cur:%A %d %B}</h2>'.title() if False else f"<h2>{cur.strftime('%A %d/%m')}</h2>")
        out.append(render_event(e))
    return "".join(out)


def render_themes(themes):
    if not themes:
        return '<div class="card meta">Aucun thème majeur détecté dans les flux.</div>'
    out = []
    for name, t in themes.items():
        n = len(t["sources"])
        conf = f"confirmé par {n} sources" if n >= 2 else "1 seule source : à recouper"
        lis = "".join(f'<li><a href="{html.escape(i["link"])}" target="_blank" rel="noopener">{html.escape(i["title"])}</a> '
                      f'<span class="meta">({html.escape(i["source"])})</span></li>' for i in t["items"])
        out.append(f'<div class="card"><b>{html.escape(name)}</b> <span class="meta">· {conf}</span>'
                   f'<div class="sc"><b>Transmission :</b> {html.escape(t["transmission"])}</div><ul>{lis}</ul></div>')
    return "".join(out)


def key_points(events, prices):
    pts = []
    high = [e for e in events if e["impact"] == "High"]
    if high:
        pts.append(f"{len(high)} annonce(s) à fort impact, première à {high[0]['dt']:%H:%M} ({high[0]['country']} {html.escape(high[0]['title'])}).")
    else:
        pts.append("Pas d'annonce à fort impact sur la fenêtre : les flux d'actualité et les niveaux techniques dominent.")
    mv = {k: v for k, v in prices.items() if k in ASSETS and v.get("d1") is not None}
    if mv:
        k = max(mv, key=lambda x: abs(mv[x]["d1"]))
        pts.append(f"Plus gros mouvement récent : {html.escape(k)} ({mv[k]['d1']:+.2f}%).")
    vix = prices.get("VIX")
    if vix:
        pts.append(f"VIX à {vix['last']:.1f} : " + ("volatilité élevée, prudence sur la taille des positions." if vix["last"] > 22 else "volatilité contenue."))
    return "<ul>" + "".join(f"<li>{p}</li>" for p in pts) + "</ul>"


def build(mode, use_demo, nav=""):
    now = datetime.now(PARIS)
    if use_demo:
        cal, prices, news = demo()
    else:
        cal, prices, news = load_calendar(), load_prices(), load_news(36 if mode == "daily" else 24 * 7)
    if not use_demo and not cal and not prices:
        raise SystemExit("Aucune donnée récupérée : page non générée (la version précédente reste en ligne).")
    cal = [e for e in cal if e["country"] in COUNTRIES]
    if mode == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        events = [e for e in cal if start <= e["dt"] < start + timedelta(days=1) and e["impact"] in ("High", "Medium")]
        title, by_day = f"Briefing du jour — {now.strftime('%d/%m/%Y')}", False
        agenda_title = "Agenda du jour (heure de Paris)"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now.weekday() >= 5:
            start += timedelta(days=7 - now.weekday())
        end = start + timedelta(days=7 - start.weekday())
        events = [e for e in cal if start <= e["dt"] < end and e["impact"] == "High"]
        title, by_day = f"Récap hebdomadaire — semaine du {start.strftime('%d/%m/%Y')}", True
        agenda_title = "Semaine à venir : annonces à fort impact (heure de Paris)"
    body = [nav, f"<h1>{title}</h1><p class='sub'>Généré le {now:%d/%m/%Y à %H:%M} (Paris)</p>",
            "<h2>À retenir</h2>", f'<div class="card">{key_points(events, prices)}</div>',
            f"<h2>{'Marchés : bilan' if mode == 'weekly' else 'Marchés : clôtures et variations'}</h2>", render_prices(prices),
            f"<h2>{agenda_title}</h2>", render_agenda(events, by_day),
            f"<h2>Actualités et géopolitique {'(7 jours)' if mode == 'weekly' else '(36 h)'}</h2>", render_themes(group_themes(news)),
            '<p class="note">Outil d\'aide à la préparation, pas un conseil en investissement. Les scénarios sont conditionnels '
            "et dépendent du régime de marché (attentes de taux, aversion au risque). Vérifie toujours les données sources. "
            "Calendrier : flux ForexFactory non officiel ; prix : Yahoo Finance (différés).</p>"]
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{title}</title><style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "weekly", "site"])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    if a.mode == "site":  # utilisé par GitHub Actions : génère tout le site dans ./site
        site = Path("site")
        site.mkdir(exist_ok=True)
        nav = '<p><a href="daily.html">Daily</a> · <a href="weekly.html">Weekly</a></p>'
        for m in ("daily", "weekly"):
            (site / f"{m}.html").write_text(build(m, a.demo, nav), encoding="utf-8")
        (site / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=daily.html"><a href="daily.html">Daily</a>',
            encoding="utf-8")
        print("OK -> site/")
        sys.exit(0)
    out = Path(f"briefing_{a.mode}.html")
    out.write_text(build(a.mode, a.demo), encoding="utf-8")
    print(f"OK -> {out.resolve()}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
