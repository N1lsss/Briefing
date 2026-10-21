"""Analyses, graphiques SVG, agenda enrichi et résumés d'articles (gratuit, sans IA externe)."""
import html, json, math, os, re, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

H = html.escape
UA = "Mozilla/5.0 (briefing-perso)"
CCY = {"Nasdaq 100": {"USD"}, "S&P 500": {"USD"}, "CAC 40": {"EUR"}, "XAUUSD (or)": {"USD"},
       "EURUSD": {"USD", "EUR"}, "GBPUSD": {"USD", "GBP"}}


def nf(v):
    return f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"


# ------------------------------------------------------------ graphiques SVG
def chart(hist, w=640, h=190, small=False, levels=None):
    c, d, full = hist["close"][-60:], hist["dates"][-60:], hist["close"]
    if len(c) < 5:
        return ""
    pl, pr, pt, pb = (44 if small else 56), 8, 8, 20
    lo, hi = min(c), max(c)
    sp = (hi - lo) or 1
    X = lambda i: pl + i * (w - pl - pr) / (len(c) - 1)
    Y = lambda v: pt + (hi - v) * (h - pt - pb) / sp
    P = [(X(i), Y(v)) for i, v in enumerate(c)]
    j = lambda pts: " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    s = [f'<svg viewBox="0 0 {w} {h}" class="ch" role="img">']
    for v in ([lo, hi] if small else [lo, (lo + hi) / 2, hi]):
        s.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="gr"/>'
                 f'<text x="{pl - 4}" y="{Y(v) + 3:.1f}" text-anchor="end" class="tx">{nf(v)}</text>')
    s.append(f'<polygon points="{pl},{h - pb} {j(P)} {P[-1][0]:.1f},{h - pb}" class="ar"/>')
    off = len(full) - len(c)
    sm = [(X(i), min(max(Y(sum(full[off + i - 19:off + i + 1]) / 20), pt), h - pb))
          for i in range(len(c)) if off + i >= 19]
    if not small and len(sm) > 1:
        s.append(f'<polyline points="{j(sm)}" class="sm"/>')
    for name, v in (levels or {}).items():
        if lo <= v <= hi:
            s.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="lv"/>'
                     f'<text x="{w - pr}" y="{Y(v) - 3:.1f}" text-anchor="end" class="tx">{name} {nf(v)}</text>')
    s.append(f'<polyline points="{j(P)}" class="cl"/><polyline points="{j(P[-6:])}" class="c5"/>'
             f'<circle cx="{P[-1][0]:.1f}" cy="{P[-1][1]:.1f}" r="3" class="dot"/>')
    for i, a in ((0, "start"), (len(c) // 2, "middle"), (len(c) - 1, "end")):
        s.append(f'<text x="{X(i):.1f}" y="{h - 4}" text-anchor="{a}" class="tx">{d[i]}</text>')
    return "".join(s) + "</svg>"


# ------------------------------------------------------------ analyse technique + macro
def tech(hist):
    c, hh, ll = hist["close"], hist["high"], hist["low"]
    n = len(c)
    sma = lambda k: sum(c[-k:]) / k
    g = [max(c[i] - c[i - 1], 0) for i in range(n - 14, n)]
    l = [max(c[i - 1] - c[i], 0) for i in range(n - 14, n)]
    rsi = 100 if sum(l) == 0 else 100 - 100 / (1 + sum(g) / sum(l))
    atr = sum(max(hh[i] - ll[i], abs(hh[i] - c[i - 1]), abs(ll[i] - c[i - 1])) for i in range(n - 14, n)) / 14
    return {"last": c[-1], "s20": sma(20), "s50": sma(50), "rsi": rsi, "atr": atr,
            "sup": min(ll[-20:]), "res": max(hh[-20:]),
            "ch5": (c[-1] / c[-6] - 1) * 100, "ch20": (c[-1] / c[-21] - 1) * 100}


def macro(asset, ctx):
    g = lambda k: (ctx.get(k) or {}).get("w") or 0
    sg = lambda v: (v > 0) - (v < 0)
    dxy, y, vix = g("DXY (dollar)"), g("US 10 ans (%)"), (ctx.get("VIX") or {}).get("last", 18)
    s, parts = 0, []
    if asset in ("XAUUSD (or)", "EURUSD", "GBPUSD"):
        s -= sg(dxy)
        parts.append(f"dollar {'en hausse' if dxy > 0 else 'en baisse'} sur 5 séances ({dxy:+.2f} %), "
                     f"{'défavorable' if dxy > 0 else 'favorable'} à {asset.split()[0]}")
    if asset in ("XAUUSD (or)", "Nasdaq 100", "S&P 500", "CAC 40"):
        s -= sg(y)
        parts.append(f"rendement US 10 ans {'en hausse' if y > 0 else 'en baisse'} ({y:+.0f} pb), "
                     f"{'pression' if y > 0 else 'soutien'} sur les valeurs de croissance et l'or")
    if asset in ("Nasdaq 100", "S&P 500", "CAC 40"):
        s += 1 if vix < 16 else -1 if vix > 22 else 0
        parts.append(f"VIX à {vix:.1f} ({'stress élevé' if vix > 22 else 'calme' if vix < 16 else 'niveau neutre'})")
    return s, parts


def asset_card(name, p, ctx, cal, now):
    hist = p["hist"]
    if len(hist["close"]) < 51:
        return ""
    t, (ms, mparts) = tech(hist), macro(name, ctx)
    trend = 1 if t["last"] > t["s20"] > t["s50"] else -1 if t["last"] < t["s20"] < t["s50"] else 0
    score = trend + (0.5 if t["ch5"] > 0 else -0.5) + ms + (-0.5 if t["rsi"] > 70 else 0.5 if t["rsi"] < 30 else 0)
    label, cls = (("Biais haussier", "up") if score >= 1.5 else ("Biais baissier", "dn") if score <= -1.5
                  else ("Neutre / en construction", "neu"))
    a, last = t["atr"], t["last"]
    r5 = a * math.sqrt(5)
    trend_txt = ("au-dessus de la MM20 et de la MM50 : tendance haussière" if trend == 1 else
                 "sous la MM20 et la MM50 : tendance baissière" if trend == -1 else
                 "prix pris entre MM20 et MM50 : phase de transition")
    rsi_txt = (f"RSI {t['rsi']:.0f} : suracheté, risque de consolidation ou de repli" if t["rsi"] > 70 else
               f"RSI {t['rsi']:.0f} : survendu, rebond technique possible" if t["rsi"] < 30 else
               f"RSI {t['rsi']:.0f} : momentum {'positif' if t['ch5'] > 0 else 'négatif'} sans excès")
    evs = [e for e in cal if now <= e["dt"] < now + timedelta(days=7) and e["impact"] == "High" and e["country"] in CCY[name]]
    ev_txt = ("; ".join(f"{e['dt']:%d/%m %H:%M} {e['country']} {H(e['title'])}" for e in evs[:5])
              or "aucune annonce majeure identifiée sur 7 jours")
    li = [f"<b>Tendance :</b> {nf(last)} {trend_txt} (MM20 {nf(t['s20'])}, MM50 {nf(t['s50'])}).",
          f"<b>Court terme (jours) :</b> {rsi_txt} ; {t['ch5']:+.2f} % sur 5 séances.",
          f"<b>Moyen terme (semaines) :</b> {t['ch20']:+.2f} % sur 20 séances ; MM20 "
          f"{'au-dessus' if t['s20'] > t['s50'] else 'sous'} la MM50, dynamique {'haussière' if t['s20'] > t['s50'] else 'baissière'}.",
          f"<b>Volatilité :</b> amplitude moyenne ≈ {nf(a)} ({a / last * 100:.2f} %) par séance ; "
          f"fourchette statistique indicative sur 5 séances ≈ {nf(last - r5)} – {nf(last + r5)}.",
          f"<b>Macro :</b> {' ; '.join(mparts) if mparts else 'pas de lien direct simple avec le dollar ou les taux.'}",
          f"<b>Annonces à surveiller (7 j) :</b> {ev_txt}."]
    sc = (f"<b>Scénario haussier :</b> clôture au-dessus de {nf(t['res'])} (plus haut 20 j) → continuation, "
          f"extension indicative vers {nf(t['res'] + 2 * a)}.<br>"
          f"<b>Scénario baissier :</b> cassure sous {nf(t['sup'])} (plus bas 20 j) → accélération, "
          f"extension indicative vers {nf(t['sup'] - 2 * a)}.<br>"
          f"<b>Entre les deux :</b> range {nf(t['sup'])} – {nf(t['res'])}, rebonds/rejets sur les bornes "
          f"tant qu'une annonce ne force pas la sortie.")
    d1, w = p["d1"], p["w"]
    return (f'<div class="card"><div class="ah"><b>{H(name)}</b> <span class="pill {cls}">{label}</span> '
            f'<span class="meta">{nf(last)} · 1 j {d1:+.2f} % · 5 j {(w or 0):+.2f} %</span></div>'
            f'{chart(hist, levels={"Rés.": t["res"], "Sup.": t["sup"]})}<ul>{"".join(f"<li>{x}</li>" for x in li)}</ul>'
            f'<div class="sc">{sc}</div></div>')


def render_markets(prices, assets, context, cal, now):
    ctx = {k: prices[k] for k in context if k in prices}
    cards = "".join(asset_card(a, prices[a], ctx, cal, now) for a in assets if a in prices and prices[a].get("hist"))
    small = ""
    for k in context:
        p = prices.get(k)
        if p and p.get("hist"):
            u = p["unit"]
            small += (f'<div class="card"><b>{H(k)}</b> <span class="meta">{nf(p["last"])} · 1 j {p["d1"]:+.2f} {u} · '
                      f'5 j {(p["w"] or 0):+.2f} {u}</span>{chart(p["hist"], 300, 110, small=True)}</div>')
    legend = ('<p class="meta">Courbe : clôtures sur 60 séances ; trait épais : 5 dernières séances ; '
              'pointillés orange : MM20 ; traits horizontaux : résistance/support sur 20 jours.</p>')
    return (cards + legend + '<h2>Contexte : dollar, taux, volatilité, pétrole</h2>' + f'<div class="grid">{small}</div>'
            + '<p class="meta">Lectures techniques et statistiques à titre indicatif : ce ne sont pas des prévisions.</p>')


# ------------------------------------------------------------ agenda enrichi
INFO = [
    (r"core cpi", "Inflation sous-jacente (hors énergie et alimentation), la mesure que la Fed suit le plus.",
     "Écart de 0,1 pt avec le consensus = réaction nette ; regarder aussi le CPI annuel.", "USD, XAUUSD, Nasdaq, S&P 500"),
    (r"\bcpi\b", "Inflation des prix à la consommation : oriente les anticipations de taux.",
     "Comparer au consensus, au mois précédent et à la composante sous-jacente.", "USD, XAUUSD, indices"),
    (r"non-farm|nfp", "Créations d'emplois US : publication la plus volatile du mois.",
     "Créations, taux de chômage, salaires horaires et révisions des mois précédents.", "USD, XAUUSD, EURUSD, GBPUSD, indices"),
    (r"unemployment claims|jobless", "Inscriptions hebdomadaires au chômage : thermomètre rapide du marché du travail.",
     "Moyenne sur 4 semaines plutôt qu'un chiffre isolé.", "USD"),
    (r"unemployment rate", "Taux de chômage : lecture inversée (hausse = marché du travail plus faible).",
     "Lire avec les créations d'emplois et les salaires.", "USD ou devise concernée"),
    (r"fomc|federal funds|rate decision|interest rate", "Décision de taux : événement majeur de la semaine.",
     "Décision, communiqué (changements de formulation), projections et conférence de presse.", "Toutes classes d'actifs"),
    (r"main refinancing|deposit facility|ecb", "Décision de taux de la BCE.",
     "Ton de la conférence de presse de Lagarde et indications sur la suite.", "EURUSD, CAC 40"),
    (r"bank rate|boe|mpc", "Décision de taux de la Banque d'Angleterre.",
     "Répartition des votes du comité (écarts = signal fort).", "GBPUSD"),
    (r"speaks|speech|press conference", "Discours de banquier central : peut décaler les anticipations de taux.",
     "Changement de ton ou de vocabulaire par rapport à la dernière réunion.", "USD / EUR / GBP selon l'orateur"),
    (r"minutes", "Compte rendu de la dernière réunion de banque centrale.",
     "Divisions du comité et débat sur le calendrier des taux.", "Devise concernée, XAUUSD"),
    (r"retail sales", "Ventes au détail : mesure de la consommation, moteur de la croissance.",
     "Chiffre hors automobile (« control group » aux US).", "Devise concernée, indices"),
    (r"\bppi\b|pce", "Prix producteurs / dépenses de consommation : relais de l'inflation.",
     "Le PCE sous-jacent est l'indicateur préféré de la Fed.", "USD, XAUUSD"),
    (r"ism|pmi", "Indices d'activité : signal avancé de la conjoncture (seuil de 50 = expansion).",
     "Sous-indices emploi et prix payés.", "Devise concernée, indices"),
    (r"gdp", "PIB : photographie de la croissance.",
     "Révisions et composantes (consommation, investissement).", "Devise concernée, indices"),
    (r"confidence|sentiment", "Confiance des ménages/entreprises : indicateur avancé de consommation.",
     "Anticipations d'inflation incluses dans l'enquête.", "Devise concernée"),
]


def event_extra(e):
    for pat, what, watch, assets in INFO:
        if re.search(pat, e["title"], re.I):
            vol = ("Volatilité forte attendue dans les minutes suivant la publication : spreads élargis, "
                   "éviter d'entrer juste avant." if e["impact"] == "High"
                   else "Impact modéré sauf grosse surprise par rapport au consensus.")
            return (f'<div class="sc"><b>Pourquoi c\'est important :</b> {what}<br><b>À surveiller :</b> {watch}<br>'
                    f'<b>Actifs sensibles :</b> {assets}<br><span class="meta">{vol}</span></div>')
    return ""


def timeline(events):
    rows = {"09:00": ["Ouverture Europe (Paris, Francfort, Londres)"], "15:30": ["Ouverture Wall Street"],
            "22:00": ["Clôture Wall Street"]}
    hi = {}
    for e in events:
        k = f"{e['dt']:%H:%M}"
        rows.setdefault(k, [])
        rows[k].append(f"{e['country']} {e['title']}")
        hi[k] = hi.get(k, False) or e["impact"] == "High"
    badge = '<span class="badge High">High</span>'
    out = "".join(f'<div class="tl"><b>{k}</b> {badge if hi.get(k) else ""} {H(" · ".join(v))}</div>'
                  for k, v in sorted(rows.items()))
    return f'<div class="card"><b>Plan de la journée (heure de Paris)</b>{out}</div>'


# ------------------------------------------------------------ résumés d'articles
FIG = re.compile(r"(?:[$€£]\s?\d[\d.,]*\s?(?:bn|m|k|billion|million|trillion|milliards?)?|"
                 r"\d[\d.,]*\s?(?:%|percent|per cent|bps|basis points|bp\b|billion|million|trillion|milliards?|bn\b))", re.I)


def fetch_text(url, fallback):
    try:
        import requests, trafilatura
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        r.raise_for_status()
        t = trafilatura.extract(r.text, include_comments=False, include_tables=False)
        if t and len(t) > 400:
            return t
    except Exception:
        pass
    return fallback or ""


def summarize(title, text, n=3):
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)) if 40 < len(s) < 320]
    tw = set(re.findall(r"\w{4,}", title.lower()))
    sc = lambda i: len(tw & set(re.findall(r"\w{4,}", sents[i].lower()))) + 1.5 * bool(FIG.search(sents[i])) + max(0, 3 - i * .5)
    return [sents[i] for i in sorted(sorted(range(len(sents)), key=lambda i: -sc(i))[:n])]


def figures(text, k=6):
    out, seen = [], set()
    for m in FIG.finditer(text):
        v = m.group(0).strip()
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        out.append((" ".join(re.findall(r"[A-Za-zÀ-ÿ'-]+", text[max(0, m.start() - 60):m.start()])[-3:]), v))
        if len(out) == k:
            break
    return out


# ------------------------------------------------------------ résumés en français via Gemini (optionnel)
MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]  # essayés dans l'ordre
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"


def gemini_summaries(items):
    """Un seul appel pour tous les articles. Renvoie True si les résumés ont été remplacés."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not items:
        return False
    import requests
    arts = "\n\n".join(f"### ARTICLE {i}\nTitre : {it['title']}\nSource : {it['source']}\n"
                        f"Texte : {it.get('_txt', '')[:5000]}" for i, it in enumerate(items))
    prompt = ("Tu es analyste de marchés financiers. Pour chaque article ci-dessous, rédige EN FRANÇAIS : "
              "\"resume\" = 2 à 4 phrases courtes avec les faits importants pour un trader (banques centrales, "
              "inflation, géopolitique, résultats, pétrole, dollar, or, indices) ; \"cles\" = jusqu'à 6 chiffres clés "
              "sous la forme {\"label\": ..., \"valeur\": ...}. N'utilise que des informations présentes dans le texte, "
              "n'invente aucun chiffre ; si le texte est insuffisant, renvoie un résumé vide. "
              "Réponds uniquement par un JSON : [{\"id\": 0, \"resume\": [\"...\"], \"cles\": []}, ...]\n\n" + arts)
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    for model in [m for m in [os.environ.get("GEMINI_MODEL", "").strip()] + MODELS if m]:
        try:
            r = requests.post(GEMINI_URL.format(model), json=body, timeout=120,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            if r.status_code in (401, 403, 429) or (r.status_code == 400 and "API key" in r.text):
                print(f"[gemini] refus ({r.status_code}) : {r.text[:200]}", file=sys.stderr)
                return False
            if r.status_code in (400, 404):  # modèle inconnu : on essaie le suivant
                print(f"[gemini {model}] indisponible ({r.status_code})", file=sys.stderr)
                continue
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            for d in data:
                it = items[int(d["id"])]
                res = [str(x) for x in d.get("resume", []) if str(x).strip()]
                if res:
                    it["sum"], it["fr"] = res, True
                    it["figs"] = [(str(c.get("label", "")), str(c.get("valeur", ""))) for c in d.get("cles", [])
                                  if c.get("valeur")][:6]
            print(f"[gemini] résumés générés avec {model}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[gemini {model}] {e}", file=sys.stderr)
    return False


def enrich(themes, offline=False):
    todo, seen = [], set()
    for t in themes.values():
        for it in t["items"][:2]:
            if it["link"] not in seen and len(todo) < 8:
                seen.add(it["link"])
                todo.append(it)

    def work(it):
        txt = it.get("summary", "") if offline else fetch_text(it["link"], it.get("summary", ""))
        it["sum"] = summarize(it["title"], txt) if len(txt) > 80 else []
        it["figs"] = figures(txt)
        it["_txt"] = txt
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(work, todo))
    if not offline:
        gemini_summaries([it for it in todo if len(it.get("_txt", "")) > 200])
    return themes


def render_themes(themes):
    if not themes:
        return '<div class="card meta">Aucun thème majeur détecté dans les flux.</div>'
    out = []
    for name, t in themes.items():
        n = len(t["sources"])
        conf = f"confirmé par {n} sources" if n >= 2 else "1 seule source : à recouper"
        arts = ""
        for it in t["items"][:2]:
            summ = "".join(f"<p>{H(s)}</p>" for s in it.get("sum", []))
            chips = "".join(f'<span class="chip">{H(l)} <b>{H(v)}</b></span>' for l, v in it.get("figs", []))
            arts += (f'<div class="art"><b>{H(it["title"])}</b> <span class="meta">({H(it["source"])} · {it["dt"]:%d/%m %H:%M} UTC)</span>'
                     f'{summ}{"<div>" + chips + "</div>" if chips else ""}'
                     f'<a href="{H(it["link"])}" target="_blank" rel="noopener">Lire l&#39;article sur {H(it["source"])} →</a></div>')
        out.append(f'<div class="card"><b>{H(name)}</b> <span class="meta">· {conf}</span>'
                   f'<div class="sc"><b>Transmission :</b> {H(t["transmission"])}</div>{arts}</div>')
    ai = any(it.get("fr") for t in themes.values() for it in t["items"][:2])
    note = ("Résumés rédigés en français par Gemini à partir du texte des articles : vérifie les chiffres sur l'article source."
            if ai else "Résumés = extraits des phrases clés de chaque article (langue d'origine) et chiffres détectés.")
    return "".join(out) + f'<p class="meta">{note}</p>'
