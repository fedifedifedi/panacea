"""Style de Panacea : toutes les couleurs et tailles de l'interface, à un seul endroit.

Utilisé par app/main.py (CSS de l'application) et src/graph.py (couleurs du graphe).
.streamlit/config.toml reprend COLORS pour les widgets natifs de Streamlit
(ce fichier TOML ne peut pas importer Python : garder les deux alignés).
"""

# Thème actif : "dark" (défaut) ou "light". Penser à aligner .streamlit/config.toml (base = ...).
THEME = "dark"

# Deux palettes conçues ensemble (skill ui-ux-pro-max : « dark-mode-pairing »). Le mode sombre
# n'est pas une inversion : variantes tonales dédiées, contrastes vérifiés séparément.
#   bg          fond des tuiles        surface   fond de page / encarts
#   accent      remplissages (boutons, onglet actif) : texte blanc dessus ≥ 4.5:1
#   accent_ink  texte, liens et icônes verts         accent_mark  marques de graphiques
PALETTES = {
    "light": {
        "bg": "#FFFFFF", "surface": "#F6F8F7", "border": "#E3E8E6",
        "text": "#14213D", "muted": "#5B6770",
        "accent": "#0F7B5F", "accent_ink": "#0F7B5F", "accent_mark": "#0F7B5F",
        "accent_soft": "#E6F4EF", "alert": "#B54708",
        "hero_from": "#0B3D2E", "hero_to": "#14213D",
        "shadow_rgb": "20, 33, 61",
    },
    # Contrastes mesurés sur les tuiles #111A2C : texte 14.8:1, secondaire 7.2:1,
    # vert texte 9.1:1, alerte 7.4:1 ; texte blanc sur bouton #0F7B5F 5.2:1.
    "dark": {
        "bg": "#111A2C", "surface": "#0A1120", "border": "#22314B",
        "text": "#E7EDF5", "muted": "#9AA8BC",
        "accent": "#0F7B5F", "accent_ink": "#4FD1A1", "accent_mark": "#14A37F",
        "accent_soft": "#0F2E2A", "alert": "#F79009",
        "hero_from": "#0B3D2E", "hero_to": "#14213D",
        "shadow_rgb": "0, 0, 0",
    },
}
COLORS = PALETTES[THEME]

# Échelle séquentielle du score (une seule teinte). Sur fond clair : du clair au foncé ;
# sur fond sombre : du sombre au clair (la valeur la plus forte est la plus lumineuse).
SCORE_SCALE = ([[0.0, "#9CCFBD"], [0.55, "#0F7B5F"], [1.0, "#0B3D2E"]] if THEME == "light"
               else [[0.0, "#1F5A4B"], [0.55, "#14A37F"], [1.0, "#8EF0C8"]])


def plotly_layout(**overrides) -> dict:
    """Mise en forme commune des graphiques Plotly (police, couleurs, grilles discrètes)."""
    axis = dict(gridcolor=COLORS["border"], linecolor=COLORS["border"], zeroline=False,
                tickfont=dict(color=COLORS["muted"], size=12), title_font=dict(color=COLORS["muted"], size=13))
    layout = dict(
        font=dict(family="Inter, system-ui, Segoe UI, sans-serif", color=COLORS["text"], size=13),
        paper_bgcolor=COLORS["bg"], plot_bgcolor=COLORS["bg"],
        margin=dict(l=10, r=10, t=10, b=10), xaxis=axis, yaxis=dict(axis),
        hoverlabel=dict(bgcolor=COLORS["bg"], bordercolor=COLORS["border"],
                        font=dict(family="Inter, sans-serif", color=COLORS["text"], size=13)),
        showlegend=False,
    )
    layout.update(overrides)
    return layout


PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}

# Couleurs du graphe de connaissances (src/graph.py) : dérivées de COLORS
GRAPH = {
    "plant": COLORS["accent_mark"], # plantes : vert émeraude
    "indication": COLORS["text"],   # indications : couleur du texte (bleu nuit / clair)
    "compound": COLORS["muted"],    # composés : gris-bleu
    "background": COLORS["bg"],
    "edge_treats": COLORS["text"] + "33",     # bleu nuit, 20 % d'opacité
    "edge_contains": COLORS["muted"] + "40",  # gris-bleu, 25 % d'opacité
}

FONTS = {
    "ui": "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif",
    "brand": "'Cormorant Garamond', Georgia, serif",   # uniquement pour le mot « Panacea »
    "import": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
              "&family=Cormorant+Garamond:wght@600;700&display=swap",
}

# Tailles pensées pour un portable partagé en visio (texte de base 16 px, rien sous 13 px)
SIZES = {
    "base": "16px",
    "small": "0.875rem",     # 14 px : libellés, légendes
    "tiny": "0.8125rem",     # 13 px : minimum absolu
    "h_section": "1.35rem",
    "brand": "2.1rem",
    "kpi": "2rem",
    "logo": "48px",
    "radius": "16px",        # Bento : coins 16–24 px (fiche « Bento Box Grid » du skill)
    "radius_tile": "20px",   # tuiles de la grille d'accueil
    "radius_sm": "8px",
    "bento_gap": "16px",     # espacement Bento : 16 px
    "max_width": "1680px",   # pleine largeur sur portable ; borne seulement les très grands écrans
    "gap": "1rem",
    "bar_height": "10px",
    "hero_name": "3.4rem",
}


def css() -> str:
    c, f, s = COLORS, FONTS, SIZES
    return f"""
<style>
@import url('{f["import"]}');
:root {{
  --bg: {c["bg"]}; --surface: {c["surface"]}; --border: {c["border"]};
  --text: {c["text"]}; --muted: {c["muted"]}; --accent: {c["accent"]};
  --accent-soft: {c["accent_soft"]}; --alert: {c["alert"]}; --accent-ink: {c["accent_ink"]};
  --radius: {s["radius"]}; --radius-tile: {s["radius_tile"]}; --radius-sm: {s["radius_sm"]};
  --gap: {s["bento_gap"]};
  /* Ombres Bento : très douces au repos, un peu plus marquées au survol */
  --shadow-sm: 0 1px 2px rgba({c["shadow_rgb"]}, .06), 0 4px 12px rgba({c["shadow_rgb"]}, .08);
  --shadow: 0 2px 4px rgba({c["shadow_rgb"]}, .08), 0 12px 28px -8px rgba({c["shadow_rgb"]}, .30);
}}

/* ---------- Base ---------- */
/* Texte uniquement : les icônes de Streamlit (police Material) ne sont pas touchées */
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp td, .stApp th,
.stApp button p, .stApp summary, .stApp small, .stApp a, [data-testid="stMarkdownContainer"] {{
  font-family: {f["ui"]} !important; }}
/* Bento : fond de page neutre, les tuiles blanches s'en détachent */
.stApp {{ background: var(--surface); color: var(--text); font-size: {s["base"]}; }}
header[data-testid="stHeader"] {{ background: transparent; }}
/* padding-top : laisse la place à la barre d'outils de Streamlit (sinon l'en-tête est masqué) */
.block-container {{ max-width: {s["max_width"]}; padding: 3.4rem 2.2rem 2rem; }}
h1, h2, h3, h4, h5 {{ font-family: {f["ui"]} !important; color: var(--text); letter-spacing: -.01em; }}
a, .stApp [data-testid="stMarkdownContainer"] a {{ color: var(--accent-ink) !important; }}
.stApp iframe {{ border: 0 !important; outline: 0 !important; border-radius: var(--radius-sm);
  background: var(--bg); }}
[data-testid="stCaptionContainer"], .stCaption {{ color: var(--muted) !important; }}

/* ---------- Cartes : un seul style ---------- */
.pa-card {{ background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 1rem 1.15rem; box-shadow: var(--shadow-sm); transition: box-shadow .2s ease; height: 100%; }}
.pa-card:hover, div[class*="st-key-step_card_"]:hover {{ box-shadow: var(--shadow); }}
/* Conteneurs encadrés de Streamlit = tuiles blanches */
div[data-testid="stVerticalBlockBorderWrapper"] {{ border-color: var(--border) !important;
  border-radius: var(--radius) !important; background: var(--bg); box-shadow: var(--shadow-sm); }}
div[data-testid="stExpander"] details {{ border-color: var(--border) !important;
  border-radius: var(--radius) !important; background: var(--bg); }}

/* ---------- Grille Bento (tuiles statiques de l'accueil) ---------- */
.pa-bento {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--gap);
  margin: var(--gap) 0; }}
.pa-tile {{ background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-tile);
  padding: 1.3rem 1.4rem; box-shadow: var(--shadow-sm);
  transition: transform .2s ease, box-shadow .2s ease; }}
.pa-tile:hover {{ transform: translateY(-2px); box-shadow: var(--shadow); }}
.span-1 {{ grid-column: span 1; }} .span-2 {{ grid-column: span 2; }} .span-3 {{ grid-column: span 3; }}
/* Tuile chiffre clé */
.pa-kpi-tile {{ display: flex; flex-direction: column; gap: .15rem; }}
.pa-kpi-tile .ic {{ width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center;
  background: var(--accent-soft); margin-bottom: .6rem; }}
.pa-kpi-tile b {{ font-size: 2rem; line-height: 1.1; color: var(--text); font-variant-numeric: tabular-nums; }}
.pa-kpi-tile .l {{ font-weight: 600; color: var(--text); font-size: .95rem; }}
.pa-kpi-tile .n {{ color: var(--muted); font-size: {s["tiny"]}; }}
/* Tuile avertissement (seul usage de la couleur d'alerte) */
.pa-alert-tile {{ border-top: 3px solid var(--alert); display: flex; flex-direction: column; gap: .5rem; }}
.pa-alert-tile b {{ color: var(--alert); font-size: 1rem; }}
.pa-alert-tile p {{ color: var(--text); font-size: .9rem; line-height: 1.5; margin: 0; }}

/* ---------- Bandeau d'accueil (fond sombre, texte blanc) ---------- */
.st-key-hero {{ background: linear-gradient(120deg, {c["hero_from"]} 0%, {c["hero_to"]} 100%);
  border-radius: var(--radius-tile); padding: 2rem 2.3rem 1.7rem; color: #FFFFFF; height: 100%;
  box-shadow: var(--shadow-sm); border: 1px solid rgba(255,255,255,.07); }}
/* Rangée du haut : les deux tuiles ont la même hauteur */
/* (le bloc horizontal visé est celui qui contient les deux tuiles, pas celui de la recherche) */
.st-key-home_top [data-testid="stHorizontalBlock"]:has(.st-key-hero) {{ align-items: stretch; }}
.st-key-home_top [data-testid="stColumn"] *:has(.st-key-hero),
.st-key-home_top [data-testid="stColumn"] *:has(.st-key-top1) {{ height: 100%; }}
.st-key-hero {{ justify-content: center; }}
.st-key-hero [data-testid="stMarkdownContainer"], .st-key-hero p {{ color: #FFFFFF; }}
.pa-hero-brand {{ display: flex; align-items: center; gap: 1rem; }}
.pa-hero-brand img, .pa-hero-brand svg {{ width: 64px; height: 64px; border-radius: 50%;
  background: #FFFFFF; object-fit: cover; }}
.pa-hero-brand .name {{ font-family: {f["brand"]} !important; font-weight: 700; font-size: {s["hero_name"]};
  letter-spacing: .12em; line-height: 1; color: #FFFFFF; }}
.st-key-hero p.pa-hero-sub {{ font-size: 1.5rem !important; font-weight: 600; line-height: 1.3;
  margin: 1rem 0 .4rem; color: #FFFFFF; }}
.st-key-hero p.pa-hero-origin {{ font-size: .98rem !important; line-height: 1.55;
  color: rgba(255,255,255,.78) !important; max-width: 46rem; }}
.pa-hero-origin i {{ color: #FFFFFF; }}
/* Barre de recherche */
.st-key-hero_form {{ margin-top: .6rem; }}
.st-key-hero input {{ background: var(--bg) !important; color: var(--text) !important; font-size: 1.05rem !important;
  border-radius: 999px !important; padding-left: 1.2rem !important; }}
.st-key-hero input::placeholder {{ color: var(--muted) !important; opacity: 1; }}
.st-key-hero div[data-baseweb="input"], .st-key-hero div[data-baseweb="base-input"] {{
  height: 3rem; border-radius: 999px !important; background: var(--bg) !important;
  border: 1px solid rgba(255,255,255,.18) !important; }}
.st-key-hero_go button {{ height: 3rem; border-radius: 999px; font-weight: 600; border: 0;
  background: var(--accent) !important; color: #FFFFFF !important; }}
.st-key-hero_go button:hover {{ filter: brightness(1.1); }}
.pa-try {{ font-size: {s["tiny"]}; color: rgba(255,255,255,.7) !important; margin: .2rem 0 .1rem; }}
div[class*="st-key-sugg_"] button {{ background: rgba(255,255,255,.08) !important; color: #FFFFFF !important;
  border: 1px solid rgba(255,255,255,.3) !important; border-radius: 999px; font-size: .85rem;
  min-height: 2.1rem; padding: .2rem .9rem; }}
div[class*="st-key-sugg_"] button:hover {{ background: rgba(255,255,255,.18) !important; }}
div[class*="st-key-sugg_"] button p {{ color: #FFFFFF !important; font-size: .85rem; }}
/* ---------- Tuile « Piste n°1 » (claire, liseré d'accent) ---------- */
.st-key-top1 {{ background: var(--bg); border: 1px solid var(--border); border-top: 4px solid var(--accent);
  border-radius: var(--radius-tile); padding: 1.5rem 1.5rem 1.3rem; height: 100%; box-shadow: var(--shadow-sm); }}
.pa-top1 .k {{ font-size: {s["tiny"]}; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--accent-ink); }}
.pa-top1 .name {{ font-size: 1.7rem; font-weight: 700; margin-top: .4rem; color: var(--text); line-height: 1.15; }}
.pa-top1 .sci {{ font-style: italic; color: var(--muted); }}
.pa-top1 .score {{ display: flex; align-items: baseline; gap: .4rem; margin: .9rem 0 .35rem; }}
.pa-top1 .score b {{ font-size: 2.8rem; line-height: 1; color: var(--text); font-variant-numeric: tabular-nums; }}
.pa-top1 .score span {{ color: var(--muted); }}
.pa-top1 .meter {{ height: 8px; background: var(--surface); border-radius: 999px; overflow: hidden; }}
.pa-top1 .meter i {{ display: block; height: 100%; background: var(--accent); border-radius: 999px; }}
.pa-top1 .facts {{ display: grid; grid-template-columns: 1fr 1fr; gap: .6rem; margin: 1rem 0 .9rem; }}
.pa-top1 .facts div {{ background: var(--surface); border-radius: 12px; padding: .6rem .75rem; }}
.pa-top1 .facts b {{ display: block; font-size: 1.45rem; color: var(--text); font-variant-numeric: tabular-nums; }}
.pa-top1 .facts span {{ font-size: {s["tiny"]}; color: var(--muted); }}
.pa-top1 p {{ font-size: .9rem; line-height: 1.5; color: var(--muted); margin: 0; }}
.st-key-lead_more {{ margin-top: .7rem; }}
.st-key-lead_more button {{ border-radius: 999px; font-weight: 600; }}

/* ---------- Découvertes : histoire en trois temps (dans une tuile Bento) ---------- */
.pa-disc .head {{ display: flex; justify-content: space-between; align-items: baseline; gap: .5rem; }}
.pa-disc .head b {{ font-size: 1.15rem; }}
.pa-disc .head i {{ color: var(--muted); font-size: .88rem; }}
.pa-disc .claim {{ font-size: .93rem; color: var(--text); margin: .3rem 0 .8rem; font-weight: 500; }}
.pa-disc ol {{ list-style: none; margin: 0; padding: 0; position: relative; }}
.pa-disc ol::before {{ content: ""; position: absolute; left: 9px; top: 10px; bottom: 10px;
  border-left: 1px dashed var(--border); }}
.pa-disc li {{ position: relative; padding: 0 0 .85rem 1.9rem; }}
.pa-disc li:last-child {{ padding-bottom: 0; }}
.pa-disc li::before {{ content: attr(data-n); position: absolute; left: 0; top: .05rem; width: 20px; height: 20px;
  border-radius: 50%; background: var(--accent-soft); color: var(--accent-ink); font-size: .72rem; font-weight: 700;
  display: grid; place-items: center; }}
.pa-disc li.end::before {{ background: var(--accent); color: #FFFFFF; }}
.pa-disc .lbl {{ font-size: .72rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--muted); }}
.pa-disc .txt {{ font-size: .9rem; line-height: 1.45; margin-top: .1rem; }}
.pa-disc q {{ font-style: italic; color: var(--text); }}
.pa-disc a {{ font-size: {s["tiny"]}; white-space: nowrap; }}
.pa-disc .proof {{ margin-top: .8rem; font-size: {s["tiny"]}; color: var(--accent-ink); background: var(--accent-soft);
  border-radius: 8px; padding: .4rem .6rem; }}

/* ---------- Titres de section ---------- */
.pa-section {{ margin: 1.6rem 0 .6rem; }}
.pa-section h3 {{ font-size: {s["h_section"]}; font-weight: 600; margin: 0; }}
.pa-section p {{ color: var(--muted); margin: .2rem 0 0; font-size: .95rem; }}

/* ---------- Pipeline compact (une ligne) ---------- */
div[class*="st-key-step_card_"] {{ position: relative; overflow: visible !important; padding: .55rem .8rem !important; }}
div[class*="st-key-step_card_"]:not(.st-key-step_card_4)::after {{ content: "→"; position: absolute;
  right: -1.05rem; top: 50%; transform: translateY(-50%); color: var(--muted); font-size: 1rem; }}
.pa-step {{ display: flex; align-items: center; gap: .6rem; }}
.pa-step svg {{ flex: 0 0 auto; }}
.pa-step .num {{ color: var(--accent-ink); font-weight: 700; font-size: .72rem; letter-spacing: .06em; }}
.pa-step .t {{ font-weight: 600; font-size: .93rem; color: var(--text); line-height: 1.2; }}
div[class*="st-key-step_"] button {{ color: var(--accent-ink) !important; font-size: .8rem; padding: 0; min-height: 0; }}
div[class*="st-key-step_"] button p {{ font-size: .8rem; }}

/* ---------- Avertissement médical (seul usage de la couleur d'alerte) ---------- */
.pa-notice {{ border: 1px solid var(--border); border-left: 3px solid var(--alert);
  background: var(--bg); border-radius: var(--radius-sm); padding: .55rem .95rem;
  font-size: .9rem; color: var(--text); margin: 1.2rem 0 .4rem; }}
.pa-notice b {{ color: var(--alert); }}

/* ---------- Onglets ---------- */
/* Navigation en pastilles : barre blanche arrondie, onglet actif en vert plein */
/* (Streamlit 1.67 : onglets = div[role=tab] / data-testid=stTab dans un div[role=tablist]) */
div[role="tablist"] {{ background: var(--bg); border: 1px solid var(--border); border-radius: 999px;
  padding: 6px; gap: 6px; width: fit-content; max-width: 100%; box-shadow: var(--shadow-sm);
  margin: .4rem 0 1.2rem; }}
div:has(> div[role="tablist"]) {{ border-bottom: 0 !important; box-shadow: none !important; }}
div:has(> div[role="tablist"])::after, div:has(> div[role="tablist"])::before {{ display: none !important; }}
[data-testid="stTab"] .react-aria-SelectionIndicator {{ display: none !important; }}
[data-testid="stTab"] {{ border-radius: 999px !important; padding: .55rem 1.15rem !important;
  min-height: 44px; display: flex; align-items: center; cursor: pointer; transition: background .2s ease; }}
[data-testid="stTab"] p {{ font-size: .95rem; font-weight: 600; color: var(--muted) !important; margin: 0; }}
[data-testid="stTab"] p span[role="img"] {{ color: var(--muted); margin-right: .2rem; }}
[data-testid="stTab"]:hover {{ background: var(--surface); }}
[data-testid="stTab"][aria-selected="true"] {{ background: var(--accent) !important; }}
[data-testid="stTab"][aria-selected="true"] p,
[data-testid="stTab"][aria-selected="true"] p span[role="img"] {{ color: #FFFFFF !important; }}
[data-testid="stTab"]:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* Sur-titre de section (« 01 · Histoires ») */
.pa-section .kicker {{ display: block; font-size: .75rem; font-weight: 700; letter-spacing: .1em;
  text-transform: uppercase; color: var(--accent-ink); margin-bottom: .25rem; }}

/* Découvertes « avant / après » */
.pa-ab {{ display: grid; grid-template-columns: 1fr 1.15fr; gap: .9rem; }}
.pa-ab .lbl {{ display: block; font-size: .7rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); margin-bottom: .35rem; }}
.pa-ab .lbl.now {{ color: var(--accent-ink); }}
.pa-ab .old {{ background: var(--surface); border-radius: 14px; padding: .9rem 1rem; }}
.pa-ab blockquote {{ margin: 0 0 .5rem; padding: 0; border: 0; font-family: {f["brand"]} !important;
  font-style: italic; font-size: 1.12rem; line-height: 1.35; color: var(--text) !important; }}
.pa-ab .new {{ padding: .2rem .1rem; }}
.pa-ab .new .row {{ padding: .45rem 0; border-bottom: 1px dashed var(--border); }}
.pa-ab .new .row:last-child {{ border-bottom: 0; }}
.pa-ab .new .row .lbl {{ margin-bottom: .1rem; }}

/* Tuile de synthèse (onglet Extraction) */
.pa-stats .stat b {{ font-size: 2.3rem; line-height: 1; color: var(--text); font-variant-numeric: tabular-nums; }}
.pa-stats .stat span {{ display: block; color: var(--muted); font-size: .9rem; margin: .2rem 0 .8rem; }}
.pa-stats .split {{ height: 10px; border-radius: 999px; background: var(--muted); overflow: hidden; }}
.pa-stats .split i {{ display: block; height: 100%; background: var(--accent); }}
.pa-stats .legend {{ display: flex; gap: 1rem; font-size: {s["tiny"]}; color: var(--muted); margin: .45rem 0 1rem; }}
.pa-stats .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: .35rem; }}
.pa-stats .dot.a {{ background: var(--accent); }} .pa-stats .dot.b {{ background: var(--muted); }}
.pa-stats .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: .6rem; }}
.pa-stats .grid div {{ background: var(--surface); border-radius: 12px; padding: .65rem .75rem; }}
.pa-stats .grid b {{ display: block; font-size: 1.25rem; color: var(--text); font-variant-numeric: tabular-nums; }}
.pa-stats .grid span {{ font-size: {s["tiny"]}; color: var(--muted); line-height: 1.3; }}

/* 3 premières pistes empilées à côté du nuage de points */
.pa-lead-card {{ margin-bottom: var(--gap); }}
.pa-lead-card:last-child {{ margin-bottom: 0; }}

/* ---------- Badges, citations, sources ---------- */
.pa-badge {{ display: inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .78rem;
  font-weight: 500; background: var(--surface); border: 1px solid var(--border); color: var(--muted);
  margin: 0 .3rem .3rem 0; }}
.pa-badge.accent {{ background: var(--accent-soft); border-color: transparent; color: var(--accent-ink); }}
.pa-quote {{ border-left: 2px solid var(--border); padding: .3rem .85rem; color: var(--text);
  font-size: .93rem; line-height: 1.55; margin: .35rem 0; }}
.pa-sources {{ list-style: none; padding: 0; margin: 0; }}
.pa-sources li {{ padding: .45rem 0; border-bottom: 1px solid var(--border); font-size: .92rem; }}
.pa-sources li:last-child {{ border-bottom: 0; }}
.pa-sources .n {{ display: inline-block; min-width: 1.9rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
.pa-sources details summary {{ cursor: pointer; list-style: none; }}
.pa-sources details summary::-webkit-details-marker {{ display: none; }}
.pa-sources small {{ color: var(--muted); }}

/* ---------- Questions : deux blocs distincts ---------- */
.st-key-ans_old, .st-key-ans_new {{ background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius); padding: .9rem 1.1rem; }}
.st-key-ans_old {{ border-left: 3px solid var(--muted); }}
.st-key-ans_new {{ border-left: 3px solid var(--accent); }}
.pa-block-title {{ font-weight: 600; font-size: 1rem; margin: 0 0 .3rem; color: var(--text); }}
.pa-block-title span {{ color: var(--muted); font-weight: 400; }}

/* ---------- Pistes : barres horizontales ---------- */
.pa-lead-card .rank {{ color: var(--accent-ink); font-weight: 600; font-size: {s["tiny"]}; letter-spacing: .06em; }}
.pa-lead-card .name {{ font-weight: 600; font-size: 1.1rem; margin-top: .2rem; }}
.pa-lead-card .sci {{ color: var(--muted); font-style: italic; font-size: .9rem; }}
.pa-lead-card .score {{ font-size: 1.6rem; font-weight: 700; color: var(--text); margin-top: .4rem;
  font-variant-numeric: tabular-nums; }}
.pa-lead-card .score span {{ font-size: .85rem; color: var(--muted); font-weight: 500; }}
.pa-lead-card p {{ color: var(--muted); font-size: .88rem; margin: .4rem 0 0; line-height: 1.45; }}
.pa-bars {{ font-size: .92rem; }}
.pa-bars .row {{ display: grid; grid-template-columns: 2rem minmax(10rem, 17rem) 1fr 3rem; gap: .8rem;
  align-items: center; padding: .28rem .4rem; border-radius: var(--radius-sm); }}
.pa-bars .track {{ border-left: 1px solid var(--border); }}
.pa-bars .r {{ color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }}
.pa-bars .p {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.pa-bars .p i {{ color: var(--muted); font-size: .82rem; }}
.pa-bars .track {{ height: {s["bar_height"]}; position: relative; display: block; }}
.pa-bars .bar {{ display: block; height: 100%; background: var(--accent); border-radius: 0 4px 4px 0;
  min-width: 2px; }}
.pa-bars .row:hover {{ background: var(--surface); }}
.pa-bars .val {{ font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }}
.pa-axis {{ color: var(--muted); font-size: {s["tiny"]}; }}

/* ---------- Accessibilité et adaptation (règles du skill ui-ux-pro-max) ---------- */
/* Focus clavier toujours visible (ux : « Focus States », gravité haute) */
.stApp a:focus-visible, .stApp summary:focus-visible, .stApp button:focus-visible {{
  outline: 2px solid var(--accent) !important; outline-offset: 2px; border-radius: 6px; }}
.st-key-hero a:focus-visible, .st-key-hero button:focus-visible {{ outline-color: #FFFFFF !important; }}
/* Éléments cliquables : curseur explicite */
.pa-disc a, .pa-sources a, .pa-sources summary, .pa-bars .row {{ cursor: pointer; }}
/* Cibles d'au moins 24 px (WCAG 2.2 « Target Size Minimum ») */
div[class*="st-key-step_"] button {{ min-height: 24px !important; }}
/* Mouvement réduit si l'utilisateur le demande (ux : « Reduced Motion ») */
@media (prefers-reduced-motion: reduce) {{
  .pa-tile:hover {{ transform: none; }}
  *, *::before, *::after {{ transition: none !important; animation: none !important; }}
}}
/* Pas de défilement horizontal sur écran étroit (ux : « Horizontal Scroll ») */
@media (max-width: 1000px) {{
  /* Grille Bento : 2 colonnes, les grandes tuiles occupent toute la largeur */
  .pa-bento {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .span-3 {{ grid-column: span 2; }}
  .st-key-hero {{ padding: 1.5rem 1.4rem; }}
  .pa-hero-brand .name {{ font-size: 2.5rem; }}
  /* Rangée du haut : les deux tuiles s'empilent */
  .st-key-home_top [data-testid="stHorizontalBlock"]:has(.st-key-hero) {{ flex-wrap: wrap; }}
  .st-key-home_top [data-testid="stHorizontalBlock"]:has(.st-key-hero) > [data-testid="stColumn"] {{
    min-width: 100%; }}
  .pa-alert-tile {{ grid-column: span 2; }}
  /* Pipeline : plusieurs lignes, sans flèches */
  .st-key-pipe [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
  .st-key-pipe [data-testid="stColumn"] {{ min-width: calc(33% - 1rem); }}
  div[class*="st-key-step_card_"]::after {{ display: none; }}
}}
@media (max-width: 640px) {{
  .block-container {{ padding: 3.4rem 1rem 2rem; }}
  .pa-ab {{ grid-template-columns: 1fr; }}
  div[role="tablist"] {{ border-radius: 16px; flex-wrap: wrap; }}
  .pa-bento {{ grid-template-columns: 1fr; }}
  .span-2, .span-3 {{ grid-column: span 1; }}
  .pa-bars .row {{ grid-template-columns: 1.6rem minmax(6rem, 1fr) 1fr 2.6rem; gap: .5rem; }}
  div[class*="st-key-step_card_"]::after {{ display: none; }}
}}

/* ---------- Pied de page ---------- */
.pa-footer {{ margin-top: 2.5rem; padding-top: .9rem; border-top: 1px solid var(--border);
  color: var(--muted); font-size: {s["tiny"]}; text-align: center; }}
.pa-footer a {{ color: var(--muted); text-decoration: underline; text-decoration-color: var(--border); }}
</style>
"""
