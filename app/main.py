"""Panacea — interface Streamlit (style « biotech moderne »).

Lancement (depuis la racine du projet) :
    .\\.venv\\Scripts\\streamlit run app/main.py

En-tête, « Pourquoi Panacea ? », chiffres clés, pipeline, puis quatre onglets :
Extraction, Graphe, Pistes, Questions. Seul l'onglet ouvert est calculé ; l'index
RAG et le modèle d'embeddings sont chargés une seule fois (st.cache_resource).
Toutes les couleurs et tailles sont dans app/style.py.
"""

import html
import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
ASSETS = Path(__file__).parent / "assets"

import plotly.graph_objects as go  # noqa: E402

from app.style import COLORS, PLOTLY_CONFIG, SCORE_SCALE, THEME, css, plotly_layout  # noqa: E402

# Icône d'onglet : le médaillon du logo plutôt qu'un emoji (checklist ui-ux-pro-max)
_mark = ASSETS / "panacea_mark.png"
st.set_page_config(page_title="Panacea", page_icon=str(_mark) if _mark.exists() else None,
                   layout="wide")


def H(markup: str) -> None:
    """Affiche du HTML compact : lignes jointes par une espace (sinon Markdown verrait
    du code dans l'indentation, et des mots seraient collés d'une ligne à l'autre)."""
    text = " ".join(line.strip() for line in markup.splitlines() if line.strip())
    # Typographie française : guillemets jamais séparés de leur contenu en fin de ligne
    text = text.replace("« ", "« ").replace(" »", " »")
    st.markdown(text, unsafe_allow_html=True)


def section(title: str, caption: str | None = None, kicker: str | None = None) -> None:
    """Titre de section uniforme, avec un sur-titre optionnel (ex. « 01 · Histoires »)."""
    kick = f'<span class="kicker">{kicker}</span>' if kicker else ""
    cap = f"<p>{caption}</p>" if caption else ""
    H(f'<div class="pa-section">{kick}<h3>{title}</h3>{cap}</div>')


H(css())

# Icônes du pipeline : trait fin, couleur d'accent, même taille
ICON = {
    "book": '<path d="M5 7c4-2 8-2 12 0v17c-4-2-8-2-12 0zM17 7c4-2 8-2 12 0v17c-4-2-8-2-12 0z"/>',
    "spark": '<path d="M17 5l2.4 6.8L26 14l-6.6 2.3L17 23l-2.4-6.7L8 14l6.6-2.2z"/>',
    "molecule": '<circle cx="10" cy="11" r="3.2"/><circle cx="24" cy="11" r="3.2"/><circle cx="17" cy="23" r="3.2"/>'
                '<path d="M13.2 11h7.6M11.6 13.8l3.8 6.4M22.4 13.8l-3.8 6.4"/>',
    "search": '<circle cx="15" cy="15" r="7"/><path d="M20.2 20.2L27 27"/>',
    "chat": '<path d="M5 8h24v14H14l-6 5v-5H5z"/><path d="M10 13h14M10 17h9"/>',
    "doc": '<path d="M9 4h11l6 6v16H9z"/><path d="M20 4v6h6M13 16h9M13 20h9"/>',
    "article": '<path d="M6 6h22v20H6z"/><path d="M10 11h14M10 15h14M10 19h9"/>',
}
LEAF = ('<svg viewBox="0 0 48 48" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" '
        'role="img" aria-label="Panacea"><path d="M10 38C10 20 22 10 38 10c0 16-10 28-28 28z"/>'
        '<path d="M10 38L30 18"/></svg>').format(c=COLORS["accent_ink"])


def icon(name: str) -> str:
    return (f'<svg width="30" height="28" viewBox="0 0 34 30" fill="none" stroke="{COLORS["accent_ink"]}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICON[name]}</svg>')


# =========================================================================== #
# Données (mises en cache ; relues si le fichier change)
# =========================================================================== #
def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(show_spinner=False)
def load_json(name: str, mtime: float):  # mtime fait partie de la clé de cache
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def data(name: str):
    return load_json(name, _mtime(DATA / name))


@st.cache_resource(show_spinner=False)
def get_index():
    """Index hybride + modèle d'embeddings : chargés une seule fois pour toute l'application."""
    from src.rag import HybridIndex
    index = HybridIndex.load_or_build()
    _ = index.model  # force le chargement du modèle maintenant
    return index


@st.cache_data(show_spinner=False)
def logo_mark() -> str:
    """Médaillon du logo (48 px) en base64, ou une feuille SVG minimaliste s'il est absent."""
    import base64
    path = ASSETS / "panacea_mark.png"
    if path.exists():
        return (f'<img src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}" '
                f'alt="Logo Panacea">')
    return LEAF


def split_indication(raw: str) -> tuple[str, str]:
    m = re.match(r"^\s*(.*?)\s*\((.*)\)\s*$", raw)
    return (m.group(1), m.group(2)) if m else (raw.strip(), "")


def fmt(n: int) -> str:
    """15753 -> « 15 753 » (espace fine insécable, typographie française)."""
    return f"{n:,}".replace(",", " ")


plants = data("plants.json")
names = {p["id"]: p["name"] for p in plants}
extracted = data("extracted.json")
enriched = data("enriched.json")
leads_doc = data("leads.json")
leads = leads_doc["leads"]
abstracts = data("pubmed_abstracts.json") if (DATA / "pubmed_abstracts.json").exists() else {}

# Onglets en pastilles : libellé = icône Material + nom ; on navigue toujours par le nom court
TAB_ICONS = {"Extraction": "description", "Graphe": "hub", "Pistes": "insights", "Questions": "forum"}
TAB_LABEL = {name: f":material/{ic}: {name}" for name, ic in TAB_ICONS.items()}
TABS = list(TAB_LABEL.values())


def open_tab(name: str) -> None:
    st.session_state["onglet"] = TAB_LABEL[name]


def ask_from_home(question: str | None = None) -> None:
    """Recherche lancée depuis l'accueil : remplit la question et ouvre l'onglet Questions."""
    q = (question or st.session_state.get("hero_q") or "").strip()
    if q:
        st.session_state["question"] = q
        st.session_state["submit"] = True
        st.session_state["onglet"] = TAB_LABEL["Questions"]


st.session_state.setdefault("use_llm", True)  # case « Réponse rédigée par Gemini » (onglet Questions)
EXAMPLES = ["La grande camomille soigne-t-elle les maux de tête ?",
            "L'ail contre les infections ?",
            "Quelles plantes contre l'épilepsie ?"]
# Libellés courts des pastilles de l'accueil (la question complète est envoyée)
PILLS = ["Grande camomille et maux de tête", "L'ail contre les infections", "Plantes contre l'épilepsie"]

# =========================================================================== #
# 1. Bandeau d'accueil : identité + recherche à gauche, piste n°1 à droite
# =========================================================================== #
top = leads[0]
home_top = st.container(key="home_top")  # rangée Bento : tuile identité (2/3) + tuile piste n°1 (1/3)
hero_col, top_col = home_top.columns([2, 1], gap="medium")
with hero_col, st.container(key="hero"):
    H(f"""
    <div class="pa-hero-brand">{logo_mark()}<span class="name">PANACEA</span></div>
    <p class="pa-hero-sub">Retrouver les remèdes oubliés dans les textes anciens</p>
    <p class="pa-hero-origin">Du nom de Panacée, fille d'Asclépios et déesse du remède universel
    (grec <i>pan</i>, « tout », et <i>akos</i>, « remède »). Chaque piste reste à vérifier
    scientifiquement.</p>""")
    with st.form("hero_form", border=False):
        q_col, b_col = st.columns([5, 1], vertical_alignment="bottom")
        q_col.text_input("Question", key="hero_q", label_visibility="collapsed",
                         placeholder="Posez une question à Culpeper et à PubMed…")
        b_col.form_submit_button("Interroger", key="hero_go", on_click=ask_from_home, width="stretch")
    H('<p class="pa-try">Essayez :</p>')
    for i, (col, ex) in enumerate(zip(st.columns([1.3, 1, 1]), EXAMPLES)):
        col.button(PILLS[i], key=f"sugg_{i}", on_click=ask_from_home, args=(ex,), help=ex,
                   width="stretch")

with top_col, st.container(key="top1"):
    H(f"""
    <div class="pa-top1">
      <div class="k">Piste n°1 · à explorer en priorité</div>
      <div class="name">{html.escape(top["name"])}</div>
      <div class="sci">{html.escape(top["scientific_name"])}</div>
      <div class="score"><b>{top["score"]:.1f}</b><span>/ 100</span></div>
      <div class="meter"><i style="width:{top["score"]:.1f}%"></i></div>
      <div class="facts">
        <div><b>{top["raw"]["n_indications"]}</b><span>usages anciens chez Culpeper</span></div>
        <div><b>{fmt(top["raw"]["pubmed_total"])}</b><span>articles PubMed</span></div>
      </div>
      <p>{html.escape(top["explanation"])}</p>
    </div>""")
    st.button("Voir toutes les pistes →", key="lead_more", on_click=open_tab, args=("Pistes",),
              type="primary", width="stretch")

# =========================================================================== #
# 2. Chiffres clés : 4 tuiles Bento 1×1
# =========================================================================== #
n_gemini = sum(r.get("extracted_by") == "gemini" for r in extracted)
n_claude = sum(r.get("extracted_by") == "claude" for r in extracted)
kpis = [
    ("book", len(plants), "plantes analysées", "Culpeper, 1653"),
    ("doc", len(extracted), "fiches extraites", f"{n_gemini} par Gemini, {n_claude} par Claude"),
    ("molecule", sum(p.get("compounds_total", 0) for p in enriched), "composés connus", "Wikidata"),
    ("article", sum(l["raw"]["pubmed_total"] for l in leads), "articles PubMed",
     f"{sum(len(v['articles']) for v in abstracts.values())} résumés indexés"),
]
H('<div class="pa-bento">' + "".join(
    f'<div class="pa-tile pa-kpi-tile"><span class="ic">{icon(ic)}</span><b>{fmt(v)}</b>'
    f'<span class="l">{label}</span><span class="n">{note}</span></div>'
    for ic, v, label, note in kpis) + "</div>")

# =========================================================================== #
# 3. Découvertes : trois histoires en trois temps (sources réelles, vérifiées)
# =========================================================================== #
fox = next((r for r in extracted if r["id"] == 132), {})
fox_cardiac = [x for x in (fox.get("indications") or []) if re.search(r"heart|cardi", x, re.I)]
GUTENBERG = "https://www.gutenberg.org/ebooks/49513"
DISCOVERIES = [
    {"plant": "Ail", "sci": "Allium sativum", "claim": "Contre les venins et les « eaux corrompues »",
     "steps": [
         ("Culpeper, 1653", "« …by drinking corrupt and stinking waters » : l'ail, « thériaque du pauvre ».",
          GUTENBERG, "Texte"),
         ("Molécule", "Allicine, composé soufré formé quand on écrase la gousse.",
          "https://www.wikidata.org/wiki/Q409641", "Wikidata"),
         ("Science moderne", "Activité antimicrobienne de l'allicine étudiée (bactéries, levures).",
          "https://pubmed.ncbi.nlm.nih.gov/10594976/", "PubMed 1999"),
     ], "proof": None},
    {"plant": "Grande camomille", "sci": "Tanacetum parthenium", "claim": "Contre les maux de tête",
     "steps": [
         ("Culpeper, 1653", "« very effectual for all pains in the head coming of a cold cause »",
          GUTENBERG, "Texte"),
         ("Molécule", "Parthénolide, principal composé actif de la plante.",
          "https://www.wikidata.org/wiki/Q2662393", "Wikidata"),
         ("Science moderne", "Étudiée en prévention de la migraine (revue « Herbal therapy in migraine »).",
          "https://pubmed.ncbi.nlm.nih.gov/24867850/", "PubMed 2014"),
     ], "proof": None},
    {"plant": "Digitale", "sci": "Digitalis purpurea", "claim": "Ce que Culpeper ignorait",
     "steps": [
         ("Culpeper, 1653", "« one of the best remedies for scabby head » : plaies, scrofule, aucun usage "
          "cardiaque.", GUTENBERG, "Texte"),
         ("Molécule", "Digitoxine, glycoside cardiotonique.",
          "https://www.wikidata.org/wiki/Q423890", "Wikidata"),
         ("Science moderne", "Usage cardiaque décrit par William Withering en 1785.",
          "https://pubmed.ncbi.nlm.nih.gov/3543113/", "PubMed"),
     ], "proof": (f"Preuve de fiabilité : la fiche extraite compte {len(fox.get('indications') or [])} "
                  f"indications, dont {len(fox_cardiac)} cardiaque. Le système n'a pas inventé "
                  f"l'usage moderne.")},
]

section("Découvertes", "Ce que disait Culpeper en 1653, et ce qu'en dit la science aujourd'hui.",
        kicker="01 · Histoires")


def discovery_tile(d: dict, span: int) -> str:
    """Tuile « avant / après » : citation de 1653 à gauche, molécule et science moderne à droite."""
    (old_lbl, old_txt, old_url, old_src), *modern = d["steps"]
    rows = "".join(
        f'<div class="row"><span class="lbl">{lbl}</span>'
        f'<div class="txt">{html.escape(txt)} <a href="{url}" target="_blank">{src} ↗</a></div></div>'
        for lbl, txt, url, src in modern)
    proof = f'<div class="proof">{html.escape(d["proof"])}</div>' if d["proof"] else ""
    return (f'<div class="pa-tile pa-disc span-{span}"><div class="head"><b>{d["plant"]}</b>'
            f'<i>{d["sci"]}</i></div><div class="claim">{d["claim"]}</div>'
            f'<div class="pa-ab">'
            f'<div class="old"><span class="lbl">Avant · {old_lbl}</span>'
            f'<blockquote>{html.escape(old_txt)}</blockquote>'
            f'<a href="{old_url}" target="_blank">{old_src} ↗</a></div>'
            f'<div class="new"><span class="lbl now">Aujourd\'hui</span>{rows}</div>'
            f'</div>{proof}</div>')


WARN_ICON = (f'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="{COLORS["alert"]}" '
             f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             f'<path d="M12 3l9.5 17H2.5z"/><path d="M12 10v4M12 17.5v.01"/></svg>')
NOTICE_TILE = (f'<div class="pa-tile pa-alert-tile span-1">{WARN_ICON}'
               f'<b>Ceci n\'est pas un avis médical.</b><p>Panacea explore des textes historiques et la '
               f'littérature scientifique à des fins de recherche. Plusieurs plantes citées sont '
               f'toxiques ; consultez un professionnel de santé.</p></div>')

# Grille Bento aux tailles variées : 2 + 2, puis 3 + 1 (avertissement)
H('<div class="pa-bento">' + discovery_tile(DISCOVERIES[0], 2) + discovery_tile(DISCOVERIES[1], 2)
  + discovery_tile(DISCOVERIES[2], 3) + NOTICE_TILE + "</div>")

# =========================================================================== #
# 4. Pipeline compact, sur une ligne (chaque étape ouvre l'onglet correspondant)
# =========================================================================== #
section("Pipeline", kicker="02 · Méthode")
STEPS = [
    ("book", "Texte ancien", "Extraction"),
    ("spark", "Extraction IA", "Extraction"),
    ("molecule", "Molécules", "Graphe"),
    ("search", "Pistes", "Pistes"),
    ("chat", "Questions sourcées", "Questions"),
]
pipe_box = st.container(key="pipe")  # clé : plusieurs lignes sur écran moyen (style.py)
for i, (col, (ic, title, tab)) in enumerate(zip(pipe_box.columns(5, gap="medium"), STEPS)):
    with col, st.container(border=True, key=f"step_card_{i}"):
        H(f'<div class="pa-step">{icon(ic)}<div><div class="num">{i + 1:02d}</div>'
          f'<div class="t">{title}</div></div></div>')
        st.button(f"Ouvrir {tab} →", key=f"step_{i}", type="tertiary", on_click=open_tab, args=(tab,))
H('<div style="height:.6rem"></div>')

# 5. Onglets : seul l'onglet ouvert est calculé (on_change="rerun")
tab_ext, tab_graph, tab_leads, tab_qa = st.tabs(TABS, key="onglet", on_change="rerun")


# =========================================================================== #
# Graphiques Plotly (couleurs et mise en forme : app/style.py)
# =========================================================================== #
@st.cache_data(show_spinner=False)
def top_indications(mtime: float, n: int = 10) -> list[tuple[str, list[str]]]:
    """Indications les plus partagées : [(libellé, [plantes])], regroupées comme dans le graphe."""
    from src.graph import indication_key, split_indication as split_ind
    groups: dict[str, dict] = {}
    for r in extracted:
        name = names.get(r["id"], r["plant_name"].title())
        for raw in r.get("indications") or []:
            modern, _ = split_ind(raw)
            key = indication_key(modern)
            g = groups.setdefault(key, {"label": modern, "plants": set()})
            g["plants"].add(name)
    ranked = sorted(groups.values(), key=lambda g: (-len(g["plants"]), g["label"]))[:n]
    return [(g["label"], sorted(g["plants"])) for g in ranked]


def top_indications_figure() -> go.Figure:
    """Barres horizontales : une seule série, couleur d'accent, valeur affichée au bout."""
    rows = top_indications(_mtime(DATA / "extracted.json"))
    labels = [l for l, _ in rows]
    counts = [len(p) for _, p in rows]
    fig = go.Figure(go.Bar(
        x=counts, y=labels, orientation="h", marker=dict(color=COLORS["accent_mark"], cornerradius=4),
        text=counts, textposition="outside", textfont=dict(color=COLORS["text"], size=13),
        customdata=[", ".join(p) for _, p in rows],
        hovertemplate="<b>%{y}</b><br>%{x} plantes : %{customdata}<extra></extra>",
    ))
    fig.update_layout(**plotly_layout(height=360, bargap=.35, margin=dict(l=10, r=40, t=5, b=10)))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=COLORS["text"], size=13))
    fig.update_xaxes(title_text="Nombre de plantes", dtick=1, range=[0, max(counts) + 1.2])
    return fig


def leads_scatter_figure() -> go.Figure:
    """Usage ancien (x) vs articles PubMed (y, log) ; taille et couleur = score de piste.

    La zone en bas à droite (usage riche, peu d'articles) concentre les remèdes oubliés.
    """
    import math
    import statistics
    xs = [l["raw"]["n_indications"] for l in leads]
    ys = [max(l["raw"]["pubmed_total"], 1) for l in leads]
    scores = [l["score"] for l in leads]
    x_thr = statistics.median(xs)          # usage ancien au moins médian
    y_thr = 200                            # moins de 200 articles : peu étudiée
    labels = [l["name"] if l["rank"] <= 5 else "" for l in leads]  # étiquettes sélectives : top 5
    hover = [f"<b>{html.escape(l['name'])}</b> <i>{html.escape(l['scientific_name'])}</i><br>"
             f"Score {l['score']:.1f} / 100 · rang {l['rank']}<br>"
             f"{l['raw']['n_indications']} usages anciens · {fmt(l['raw']['pubmed_total'])} articles PubMed"
             for l in leads]
    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="markers+text", text=labels, textposition="top center",
        textfont=dict(color=COLORS["text"], size=12),
        marker=dict(size=[8 + s * .32 for s in scores], color=scores, colorscale=SCORE_SCALE,
                    opacity=.85,  # chevauchements lisibles (ui-ux-pro-max, domaine chart)
                    cmin=min(scores), cmax=max(scores), line=dict(color=COLORS["bg"], width=1.5),
                    colorbar=dict(title=dict(text="Score", font=dict(color=COLORS["muted"], size=12)),
                                  thickness=10, len=.7, outlinewidth=0,
                                  tickfont=dict(color=COLORS["muted"], size=11))),
        hovertext=hover, hoverinfo="text",
    ))
    # Zone des remèdes oubliés. Sur l'axe log : les formes prennent des valeurs brutes,
    # les annotations et la plage de l'axe prennent des log10.
    y_lo, x_hi = math.log10(min(ys) / 1.6), max(xs) + 2
    fig.add_shape(type="rect", x0=x_thr, x1=x_hi, y0=min(ys) / 1.6, y1=y_thr, layer="below",
                  fillcolor=COLORS["accent_mark"], opacity=.10, line=dict(width=0))
    fig.add_annotation(x=x_hi, y=math.log10(y_thr), xanchor="right", yanchor="bottom", showarrow=False,
                       text="<b>Zone des remèdes oubliés</b> · usage riche, peu d'articles",
                       font=dict(color=COLORS["accent_ink"], size=12))
    fig.update_layout(**plotly_layout(height=620, margin=dict(l=10, r=10, t=20, b=10)))
    fig.update_xaxes(title_text="Usages anciens chez Culpeper (nombre d'indications)",
                     range=[min(xs) - 2, x_hi])
    ticks = [t for t in (1, 3, 10, 30, 100, 300, 1000, 3000) if min(ys) / 1.6 <= t <= max(ys) * 1.8]
    fig.update_yaxes(type="log", title_text="Articles PubMed (échelle log)",
                     range=[y_lo, math.log10(max(ys) * 1.8)],
                     tickvals=ticks, ticktext=[fmt(t) for t in ticks], minor=dict(showgrid=False))
    return fig


# =========================================================================== #
# Onglet Extraction
# =========================================================================== #
def render_extraction() -> None:
    section("Fiches extraites du traité de Culpeper",
            "Indications (terme moderne et terme ancien), parties utilisées, préparations et "
            "citation exacte justifiant l'usage.")

    chart_col, stats_col = st.columns([1.75, 1], gap="medium")
    with chart_col, st.container(border=True):
        H('<p class="pa-block-title">Les 10 indications les plus fréquentes '
          f'<span>· sur {len(extracted)} fiches, survolez une barre pour voir les plantes</span></p>')
        st.plotly_chart(top_indications_figure(), config=PLOTLY_CONFIG, width="stretch")
    with stats_col:
        n = len(extracted) or 1
        n_gem = sum(r.get("extracted_by") == "gemini" for r in extracted)
        n_verified = sum(bool(r.get("source_quote_verified")) for r in extracted)
        n_high = sum(r.get("scientific_name_confidence") == "high" for r in extracted)
        avg_ind = sum(len(r.get("indications") or []) for r in extracted) / n
        H(f"""
        <div class="pa-tile pa-stats">
          <p class="pa-block-title">Synthèse de l'extraction</p>
          <div class="stat"><b>{len(extracted)}</b><span>fiches structurées</span></div>
          <div class="split" title="{n_gem} Gemini, {n - n_gem} Claude">
            <i style="width:{100 * n_gem / n:.0f}%"></i></div>
          <div class="legend"><span><em class="dot a"></em>Gemini {n_gem}</span>
            <span><em class="dot b"></em>Claude {n - n_gem}</span></div>
          <div class="grid">
            <div><b>{n_verified}/{len(extracted)}</b><span>citations vérifiées mot pour mot</span></div>
            <div><b>{n_high}/{len(extracted)}</b><span>noms latins en confiance haute</span></div>
            <div><b>{avg_ind:.1f}</b><span>indications par fiche en moyenne</span></div>
            <div><b>{len(top_indications(_mtime(DATA / "extracted.json"), 10_000))}</b>
              <span>indications distinctes</span></div>
          </div>
        </div>""")

    f1, f2 = st.columns([3, 1])
    query = f1.text_input("Filtrer par plante ou indication", placeholder="ex. jaundice, Sage, cough…")
    origin = f2.selectbox("Origine de la fiche", ["Toutes", "Gemini", "Claude"])

    rows = []
    for r in extracted:
        name = names.get(r["id"], r["plant_name"].title())
        hay = " ".join([name, r.get("scientific_name") or "", *(r.get("indications") or [])]).lower()
        if query and query.lower() not in hay:
            continue
        if origin != "Toutes" and r.get("extracted_by") != origin.lower():
            continue
        rows.append((name, r))

    overview = pd.DataFrame([{
        "Plante": name,
        "Nom scientifique": r.get("scientific_name") or "—",
        "Confiance": r.get("scientific_name_confidence"),
        "Indications": len(r.get("indications") or []),
        "Parties utilisées": ", ".join(r.get("parts_used") or []) or "—",
        "Préparations": ", ".join(r.get("preparations") or []) or "—",
        "Origine": (r.get("extracted_by") or "").capitalize(),
    } for name, r in rows])
    if overview.empty:
        st.info("Aucune fiche ne correspond au filtre.")
        return
    st.dataframe(overview, hide_index=True, width="stretch",
                 column_config={"Indications": st.column_config.NumberColumn(format="%d")})

    section("Détail des fiches")
    for name, r in rows:
        sci = r.get("scientific_name") or "nom latin non identifié"
        with st.expander(f"{name} — {sci}"):
            src = r.get("extracted_by", "")
            verified = r.get("source_quote_verified")
            H(f'<span class="pa-badge accent">Fiche : {src.capitalize()} '
              f'({html.escape(r.get("model") or "")})</span>'
              f'<span class="pa-badge">Confiance du nom : {r.get("scientific_name_confidence")}</span>'
              + ('<span class="pa-badge accent">Citation vérifiée mot pour mot</span>' if verified
                 else '<span class="pa-badge">Citation non vérifiée</span>'))
            left, right = st.columns([3, 2])
            with left:
                ind = pd.DataFrame([dict(zip(("Terme moderne", "Terme ancien (Culpeper)"),
                                             split_indication(x))) for x in (r.get("indications") or [])])
                if ind.empty:
                    st.write("Aucune indication dans le texte.")
                else:
                    st.dataframe(ind, hide_index=True, width="stretch")
            with right:
                st.markdown(f"**Parties utilisées** : {', '.join(r.get('parts_used') or []) or '—'}")
                st.markdown(f"**Préparations** : {', '.join(r.get('preparations') or []) or '—'}")
                st.markdown(f"**Posologie ancienne** : {r.get('ancient_dosage') or '—'}")
                if r.get("source_quote"):
                    st.markdown("**Citation source**")
                    H(f'<div class="pa-quote">« {html.escape(r["source_quote"])} »</div>')


# =========================================================================== #
# Onglet Graphe
# =========================================================================== #
def render_graph() -> None:
    section("Graphe de connaissances",
            "Plantes (cercles verts, taille = score de piste), indications (losanges "
            f"{'clairs' if THEME == 'dark' else 'bleu nuit'}, taille = nombre de plantes) et composés "
            "(triangles gris). Survolez un nœud pour le détail.")
    top10 = st.toggle("Top 10 des plantes seulement (graphe allégé)", value=False)
    path = DATA / ("graph_top10.html" if top10 else "graph.html")
    if top10 and not path.exists():
        with st.spinner("Génération du graphe Top 10…"):
            from src.graph import build_graph, export_html
            g, _ = build_graph(10)
            export_html(g, path)
    if path.exists():
        # Fichier généré par src/graph.py (contenu de confiance) : affiché tel quel, dans une tuile
        with st.container(border=True, key="graph_tile"):
            st.iframe(path, height=700)
    else:
        st.warning("Graphe introuvable : lancez d'abord `python src/graph.py`.")


# =========================================================================== #
# Onglet Pistes
# =========================================================================== #
def score_bars(rows: list[dict]) -> str:
    """Classement en barres horizontales (une seule série : couleur d'accent, échelle 0–100)."""
    lines = []
    for l in rows:
        tip = html.escape(f"{l['name']} — score {l['score']}/100. {l['explanation']}")
        lines.append(
            f'<div class="row" title="{tip}"><span class="r">{l["rank"]}</span>'
            f'<span class="p">{html.escape(l["name"])} <i>{html.escape(l["scientific_name"])}</i></span>'
            f'<span class="track"><span class="bar" style="width:{l["score"]:.1f}%"></span></span>'
            f'<span class="val">{l["score"]:.1f}</span></div>')
    return ('<div class="pa-bars">' + "".join(lines) + '</div>'
            '<p class="pa-axis">Score de piste innovante, sur 100. Survolez une barre pour l\'explication.</p>')


def render_leads() -> None:
    section("Pistes innovantes",
            "Plantes riches en usages anciens, dotées d'une chimie connue, mais encore peu étudiées.")

    # Bento : grande tuile du nuage de points (2/3) + les 3 premières pistes empilées (1/3)
    chart_col, top_col = st.columns([2.1, 1], gap="medium")
    with chart_col, st.container(border=True):
        H('<p class="pa-block-title">Usage ancien et recherche moderne <span>· à droite, un usage riche ; '
          'en bas, peu d\'articles ; taille et couleur = score</span></p>')
        st.plotly_chart(leads_scatter_figure(), config=PLOTLY_CONFIG, width="stretch")
    with top_col:
        H("".join(
            f'<div class="pa-card pa-lead-card"><div class="rank">N° {l["rank"]}</div>'
            f'<div class="name">{html.escape(l["name"])}</div>'
            f'<div class="sci">{html.escape(l["scientific_name"])}</div>'
            f'<div class="score">{l["score"]:.1f} <span>/ 100</span></div>'
            f'<p>{html.escape(l["explanation"])}</p></div>' for l in leads[:3]))

    # Classement (2/3) + méthode du score (1/3)
    rank_col, how_col = st.columns([1.6, 1], gap="medium")
    with rank_col, st.container(border=True):
        H(f'<p class="pa-block-title">Classement des {len(leads)} plantes</p>')
        H(score_bars(leads))

    weights = leads_doc.get("weights", {})
    with how_col, st.container(border=True):
        H('<p class="pa-block-title">Comment le score est calculé</p>')
        st.markdown(
            f"`score = 100 × ({weights.get('novelty', .5):.2f} × nouveauté + "
            f"{weights.get('tradition', .3):.2f} × tradition + {weights.get('chemistry', .2):.2f} × chimie)`")
        st.markdown(
            "- **Tradition** : nombre d'indications chez Culpeper (échelle linéaire).\n"
            "- **Chimie** : log du nombre de composés connus dans Wikidata.\n"
            "- **Nouveauté** : inverse du log du nombre d'articles PubMed (moins étudiée = plus innovante).\n\n"
            "Chaque critère est ramené entre 0 et 1 (min-max) sur les plantes classées.")
        with st.expander("Limites de ce score"):
            st.markdown(
                "- Le nombre d'indications dépend aussi de la longueur du chapitre et de l'extracteur.\n"
                "- Les composés Wikidata sont bruités et corrélés à l'effort de recherche déjà fourni "
                "(d'où un poids réduit à 20 %).\n"
                "- PubMed est interrogé sur le nom latin exact : les synonymes (ex. *Salvia rosmarinus*) "
                "ne sont pas comptés.\n"
                "- La normalisation est relative au lot : une plante très peu étudiée "
                "(*Opopanax chironium*, 3 articles) étire l'échelle de nouveauté.\n"
                "- Un score élevé désigne une **piste de recherche**, jamais un remède validé.")

    section("Détail des critères")
    table = pd.DataFrame([{
        "Rang": l["rank"], "Plante": l["name"], "Nom scientifique": l["scientific_name"],
        "Score": l["score"], "Tradition": l["criteria"]["tradition"],
        "Chimie": l["criteria"]["chemistry"], "Nouveauté": l["criteria"]["novelty"],
        "Indications": l["raw"]["n_indications"], "Composés": l["raw"]["compounds_total"],
        "Articles PubMed": l["raw"]["pubmed_total"], "Depuis 2016": l["raw"]["pubmed_since_2016"],
        "Explication": l["explanation"],
    } for l in leads])
    crit = lambda label: st.column_config.ProgressColumn(label, min_value=0, max_value=1, format="%.2f")
    st.dataframe(table, hide_index=True, width="stretch", height=520, column_config={
        "Score": st.column_config.ProgressColumn("Score /100", min_value=0, max_value=100, format="%.1f"),
        "Tradition": crit("Tradition"), "Chimie": crit("Chimie"), "Nouveauté": crit("Nouveauté"),
        "Explication": st.column_config.TextColumn("Explication", width="large"),
    })


# =========================================================================== #
# Onglet Questions (RAG)
# =========================================================================== #
SOURCE_LABEL ={"culpeper": "Culpeper", "pubmed": "PubMed", "fiche": "Fiche"}


def set_question(q: str) -> None:
    st.session_state["question"] = q
    st.session_state["submit"] = True


def link_citations(md: str, context: list[dict]) -> str:
    """[3] -> lien cliquable vers l'article pour les sources PubMed."""
    def repl(m):
        n = int(m.group(1))
        if 1 <= n <= len(context) and context[n - 1]["source"] == "pubmed":
            return f"[[{n}]]({context[n - 1]['url']})"
        return f"[{n}]"
    return re.sub(r"\[(\d{1,2})\](?!\()", repl, md)


def split_answer(md: str) -> dict:
    """Découpe la réponse Gemini selon ses titres ## en parties ancien / moderne / autre."""
    parts = {"ancien": "", "moderne": "", "autre": ""}
    current = "autre"
    for block in re.split(r"(?m)^#{1,3}\s+", md):
        if not block.strip():
            continue
        title, _, body = block.partition("\n")
        t = title.lower()
        if "savoir ancien" in t or "culpeper" in t:
            current = "ancien"
        elif "science moderne" in t or "pubmed" in t:
            current = "moderne"
        elif t.startswith("sources"):
            current = None  # remplacée par la liste cliquable ci-dessous
        else:
            body = block
        if current:
            parts[current] += body.strip() + "\n\n"
    for k in parts:
        parts[k] = parts[k].replace("Ceci n'est pas un avis médical.", "").strip()
    return parts


def source_item(n: int, d: dict) -> str:
    """Une source : numéro, type, titre (lien pour PubMed) ; le passage se déplie."""
    from src.rag import source_line
    kind = SOURCE_LABEL[d["source"]]
    badge = f'<span class="pa-badge{" accent" if d["source"] == "pubmed" else ""}">{kind}</span>'
    if d["source"] == "pubmed":
        tag = " · recherche ciblée" if d.get("targeted") else ""
        head = (f'<a href="{d["url"]}" target="_blank">{html.escape(d["title"])}</a> '
                f'<small>{d["year"]} · PMID {d["pmid"]}{tag}</small>')
    else:
        head = html.escape(source_line(d))
    return (f'<li><details><summary><span class="n">[{n}]</span>{badge}{head}</summary>'
            f'<div class="pa-quote">{html.escape(d["text"])}</div></details></li>')


def source_list(docs: list[dict]) -> str:
    return '<ol class="pa-sources">' + "".join(source_item(n, d) for n, d in enumerate(docs, 1)) + "</ol>"


def render_targeted(r: dict) -> None:
    if not r["terms"]:
        st.caption("Recherche PubMed ciblée : aucune indication identifiée dans la question.")
        return
    if not r["targeted"]:
        st.caption("Recherche PubMed ciblée : aucune plante identifiée dans la question.")
        return
    for t in r["targeted"]:
        label = f"**{t['plant']}** (*{t['scientific_name'] or '?'}*) + {' / '.join(r['terms'])}"
        if t["error"]:
            st.markdown(f"- {label} : non interrogé — {t['error']}")
        elif not t["articles"]:
            st.markdown(f"- {label} : **aucun article trouvé** pour cette combinaison.")
        else:
            st.markdown(f"- {label} : {len(t['articles'])} article(s)")
            for a in t["articles"]:
                st.markdown(f"    - [{a['title']}]({a['url']}) ({a['year']}, PMID {a['pmid']})")


def fallback_reason(r: dict) -> str:
    """Cause lisible du mode de secours, d'après les tentatives Gemini."""
    details = [a["detail"] for a in r.get("attempts", []) if a["outcome"] != "succès"]
    if not details:
        return "Gemini injoignable"
    if all("quota" in d for d in details):
        return "quota Gemini épuisé sur tous les modèles"
    if any("budget total" in d or "délai" in d for d in details):
        return "Gemini n'a pas répondu dans le délai de 30 secondes"
    if all("surchargé" in d or "quota" in d for d in details):
        return "serveurs Gemini surchargés ou quota épuisé"
    return "Gemini injoignable"


def run_question(question: str, use_llm: bool) -> dict:
    """Traite une question en montrant l'avancement : étape, modèle interrogé, temps écoulé.

    Les sources s'affichent dès la fin de la recherche ; la réponse rédigée (30 s au plus)
    les complète ensuite. L'écran n'est jamais vide pendant l'attente.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    from src.rag import answer_phase, pubmed_phase, search_phase

    t0 = time.monotonic()
    elapsed = lambda: time.monotonic() - t0  # noqa: E731
    status = st.status("Préparation…", expanded=False)
    preview = st.empty()  # aperçu des sources, remplacé par l'affichage final

    status.update(label=f"Chargement de l'index (une seule fois) · {elapsed():.1f} s")
    index = get_index()
    status.update(label=f"Étape 1/3 · Recherche dans Culpeper et les résumés PubMed · {elapsed():.1f} s")
    r = search_phase(index, question)
    status.update(label=f"Étape 2/3 · Recherche PubMed ciblée (plante + indication) · {elapsed():.1f} s")
    pubmed_phase(index, r)

    if use_llm:
        with preview.container():
            section("Sources trouvées", "La réponse rédigée arrive ; les sources sont déjà consultables.")
            with st.container(border=True):
                H(source_list(r["context"]))
        current = {"model": None}
        with ThreadPoolExecutor(max_workers=1) as pool:  # Gemini en arrière-plan, l'interface vit
            future = pool.submit(answer_phase, r, lambda m: current.update(model=m))
            while not future.done():
                status.update(label=f"Étape 3/3 · Rédaction par Gemini "
                                    f"({current['model'] or 'connexion'}) · {elapsed():.0f} s")
                time.sleep(0.5)
            future.result()
    preview.empty()

    r["timings"]["total"] = total = elapsed()
    if not use_llm:
        status.update(label=f"Recherche terminée en {total:.1f} s", state="complete")
    elif r["answer"]:
        status.update(label=f"Réponse rédigée par {r['model']} en {total:.1f} s", state="complete")
    else:
        status.update(label=f"Mode de secours après {total:.1f} s : {fallback_reason(r)}", state="error")
    return r


def render_questions() -> None:
    section("Interroger Culpeper et PubMed",
            "La recherche croise le texte de 1653, les résumés PubMed et les fiches Panacea "
            "(recherche hybride BM25 + sémantique), puis Gemini rédige une réponse sourcée.")

    st.caption("Exemples")
    for col, ex in zip(st.columns(len(EXAMPLES)), EXAMPLES):
        col.button(ex, on_click=set_question, args=(ex,), width="stretch")

    with st.form("qa"):
        st.text_input("Votre question (français ou anglais)", key="question",
                      placeholder="ex. Quelles plantes contre la jaunisse ?")
        a, b = st.columns([1, 3])
        b.checkbox("Réponse rédigée par Gemini", key="use_llm",
                   help="Décochez pour la recherche seule (aucun appel à Gemini).")
        submitted = a.form_submit_button("Rechercher", type="primary", width="stretch")

    question = (st.session_state.get("question") or "").strip()
    if (submitted or st.session_state.pop("submit", False)) and question:
        answers = st.session_state.setdefault("answers", {})  # évite de rappeler Gemini
        use_llm = bool(st.session_state.get("use_llm", True))
        key = (question, use_llm)
        if key in answers:
            r = answers[key]
            st.status(f"Réponse déjà obtenue dans cette session ({r['model'] or 'recherche seule'})",
                      state="complete")
        else:
            r = run_question(question, use_llm)
            if not use_llm or r["answer"]:
                answers[key] = r  # on ne mémorise pas un mode de secours
        st.session_state["last"] = r

    r = st.session_state.get("last")
    if not r:
        return
    section(f"« {html.escape(r['question'])} »")
    if r["expansion"]:
        st.caption("Requête enrichie avec : " + ", ".join(r["expansion"]))

    if r["answer"]:
        parts = split_answer(r["answer"])
        with st.container(key="ans_old"):
            H('<p class="pa-block-title">Savoir ancien <span>(Culpeper, 1653)</span></p>')
            st.markdown(link_citations(parts["ancien"] or "_Rien trouvé dans les sources anciennes._",
                                       r["context"]))
        with st.container(key="ans_new"):
            H('<p class="pa-block-title">Science moderne <span>(PubMed)</span></p>')
            st.markdown(link_citations(parts["moderne"] or "_Aucun article PubMed fourni ne traite "
                                       "de cette question._", r["context"]))
        if parts["autre"]:
            st.markdown(link_citations(parts["autre"], r["context"]))
        total = r.get("timings", {}).get("total")
        st.caption(f"Réponse rédigée par {r['model']}"
                   + (f" en {total:.1f} s" if total else "") + " à partir des seules sources ci-dessous.")
        section("Sources")
        with st.container(border=True):
            H(source_list(r["context"]))
    else:
        if r["error"]:
            H(f'<div class="pa-notice">Réponse rédigée <b>indisponible</b> : {html.escape(fallback_reason(r))}. '
              'Voici les passages les plus pertinents et leurs sources.</div>')
            with st.expander("Détail technique (tentatives Gemini)"):
                if r.get("attempts"):
                    st.dataframe(pd.DataFrame([{
                        "Modèle": a["model"], "Résultat": a["outcome"], "Cause": a["detail"] or "—",
                        "Durée (s)": round(a["seconds"], 1)} for a in r["attempts"]]),
                        hide_index=True, width="stretch")
                st.code(r["error"])
        section("Passages les plus pertinents")
        with st.container(border=True):
            H(source_list(r["top"]))

    with st.expander("Recherche PubMed ciblée (plante + indication)", expanded=not r["answer"]):
        render_targeted(r)


# Seul l'onglet ouvert est calculé (graphe, tableaux…) : page plus légère en visio
for tab, render in ((tab_ext, render_extraction), (tab_graph, render_graph),
                    (tab_leads, render_leads), (tab_qa, render_questions)):
    if tab.open is not False:  # None si le suivi d'état est indisponible : on affiche
        with tab:
            render()

# =========================================================================== #
# Pied de page
# =========================================================================== #
H("""
<div class="pa-footer">Panacea — prototype réalisé par Fedi Limem, élève ingénieur IA &amp; Data Science
 · Sources : <a href="https://www.gutenberg.org/ebooks/49513" target="_blank">Projet Gutenberg</a>,
 <a href="https://www.wikidata.org" target="_blank">Wikidata</a>,
 <a href="https://pubmed.ncbi.nlm.nih.gov" target="_blank">PubMed</a>
 · Ceci n'est pas un avis médical.</div>""")
