"""Étape 5 : graphe de connaissances plantes / indications / composés.

Sources : data/leads.json (score, rang, explication), data/enriched.json
(indications, citations, composés) et data/plants.json (texte de Culpeper,
pour retrouver la phrase exacte qui mentionne chaque indication).

Sortie : data/graph.html (pyvis, autonome, ouvrable hors ligne), avec légende
et cases à cocher "Top 10 seulement" / "Afficher les composés".

Usage : python src/graph.py [--top N] [--output data/graph.html]
        --top N : n'exporte que les N meilleures plantes (graphe plus léger)
"""

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx
from pyvis.network import Network

# Couleurs : fichier de style unique de l'application (app/style.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.style import COLORS, GRAPH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LEADS_PATH = DATA_DIR / "leads.json"
ENRICHED_PATH = DATA_DIR / "enriched.json"
PLANTS_PATH = DATA_DIR / "plants.json"
OUTPUT_PATH = DATA_DIR / "graph.html"

TOP_FILTER = 10  # nombre de plantes gardées par la case "Top 10 seulement"

BACKGROUND = GRAPH["background"]
INK = COLORS["text"]

STYLE = {
    "plant":      {"color": GRAPH["plant"], "shape": "dot",      "label": "Plante (taille = score de piste)"},
    "indication": {"color": GRAPH["indication"], "shape": "diamond",  "label": "Indication (taille = nb de plantes)"},
    "compound":   {"color": GRAPH["compound"], "shape": "triangle", "label": "Composé chimique (Wikidata)"},
}


# --------------------------------------------------------------------------- #
# Indications : regroupement et citation
# --------------------------------------------------------------------------- #
def split_indication(raw: str) -> tuple[str, str | None]:
    """'jaundice (yellow jaundice)' -> ('jaundice', 'yellow jaundice')."""
    m = re.match(r"^\s*(.*?)\s*\((.*)\)\s*$", raw)
    modern, old = (m.group(1), m.group(2)) if m else (raw.strip(), None)
    return modern, old


def indication_key(modern: str) -> str:
    """Clé de regroupement : minuscules, espaces, ponctuation et pluriel en -s normalisés.

    'Liver obstructions' et 'liver obstruction' -> 'liver obstruction'
    (on épargne -ss, -is, -us : 'abscess', 'arthritis', 'pus').
    """
    words = re.sub(r"\s+", " ", modern.lower()).strip(" .,;").split()
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "is", "us"))
                    else w for w in words)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).lower()


def find_quote(text: str, terms: list[str], fallback: str | None) -> tuple[str | None, bool]:
    """Phrase de Culpeper contenant le terme (ancien d'abord, puis moderne).

    Renvoie (citation, exacte) ; exacte=False si on retombe sur la citation
    générale de la fiche (source_quote).
    """
    segments = re.split(r"(?<=[.;:])\s+", text)
    for term in terms:
        if not term or len(term) < 4:
            continue
        t = _norm(term)
        for seg in segments:
            if t in _norm(seg):
                seg = seg.strip()
                return (seg if len(seg) <= 320 else seg[:317] + "…"), True
    return fallback, False


# --------------------------------------------------------------------------- #
# Construction du graphe networkx
# --------------------------------------------------------------------------- #
def build_graph(top: int | None) -> tuple[nx.Graph, dict]:
    leads = json.loads(LEADS_PATH.read_text(encoding="utf-8"))["leads"]
    enriched = {p["id"]: p for p in json.loads(ENRICHED_PATH.read_text(encoding="utf-8"))}
    texts = {p["id"]: p["text"] for p in json.loads(PLANTS_PATH.read_text(encoding="utf-8"))}
    if top:
        leads = leads[:top]

    G = nx.Graph()
    # citations par indication : key -> liste de {plant, old, quote, exact}
    ind_quotes: dict[str, list[dict]] = defaultdict(list)
    ind_label: dict[str, str] = {}

    for lead in leads:
        p = enriched[lead["id"]]
        pid = f"plant:{lead['id']}"
        G.add_node(pid, kind="plant", label=lead["name"], score=lead["score"],
                   rank=lead["rank"], scientific_name=lead["scientific_name"],
                   explanation=lead["explanation"], raw=lead["raw"])

        for raw in p.get("indications") or []:
            modern, old = split_indication(raw)
            key = indication_key(modern)
            if not key:
                continue
            nid = f"ind:{key}"
            ind_label.setdefault(key, modern)
            if not G.has_node(nid):
                G.add_node(nid, kind="indication", label=modern)
            G.add_edge(pid, nid, kind="treats")
            quote, exact = find_quote(texts[lead["id"]], [old, modern], p.get("source_quote"))
            ind_quotes[key].append({"plant": lead["name"], "old": old, "quote": quote, "exact": exact})

        for c in p.get("compounds") or []:
            nid = f"cpd:{c['wikidata_id']}"
            if not G.has_node(nid):
                G.add_node(nid, kind="compound", label=c["name"], wikidata_id=c["wikidata_id"])
            G.add_edge(pid, nid, kind="contains")

    for key, quotes in ind_quotes.items():
        G.nodes[f"ind:{key}"]["quotes"] = quotes

    # Marque les nœuds visibles dans la vue "Top 10" (plante du top ou voisin d'une telle plante)
    top_plants = {n for n, d in G.nodes(data=True) if d["kind"] == "plant" and d["rank"] <= TOP_FILTER}
    for n, d in G.nodes(data=True):
        d["in_top"] = n in top_plants or any(nb in top_plants for nb in G.neighbors(n))
    return G, ind_label


# --------------------------------------------------------------------------- #
# Infobulles (HTML, converties en éléments DOM côté navigateur)
# --------------------------------------------------------------------------- #
def esc(s) -> str:
    return html.escape(str(s))


def tooltip(n: str, d: dict, G: nx.Graph) -> str:
    if d["kind"] == "plant":
        r = d["raw"]
        return (f"<b>{esc(d['label'])}</b> — <i>{esc(d['scientific_name'])}</i><br>"
                f"Score de piste : <b>{d['score']}</b> / 100 (rang {d['rank']})<br>"
                f"{esc(d['explanation'])}<br>"
                f"<small>{r['pubmed_since_2016']} articles PubMed depuis 2016 · "
                f"{G.degree(n)} liens dans le graphe</small>")
    if d["kind"] == "indication":
        parts = [f"<b>{esc(d['label'])}</b> — {len(d['quotes'])} plante(s)"]
        for q in d["quotes"]:
            old = f" ({esc(q['old'])})" if q["old"] else ""
            note = "" if q["exact"] else " <small>[citation générale de la fiche]</small>"
            quote = f"« {esc(q['quote'])} »" if q["quote"] else "<i>aucune citation</i>"
            parts.append(f"<b>{esc(q['plant'])}</b>{old} : {quote}{note}")
        return "<br>".join(parts)
    plants = sorted(G.nodes[nb]["label"] for nb in G.neighbors(n))
    return (f"<b>{esc(d['label'])}</b> ({esc(d['wikidata_id'])})<br>"
            f"Trouvé dans : {esc(', '.join(plants))}")


# --------------------------------------------------------------------------- #
# Export pyvis
# --------------------------------------------------------------------------- #
EXTRA_HTML = """
<style>
  html, body { background: __BG__; margin: 0; height: 100%; overflow: hidden; }
  /* Pas de barre de chargement : le graphe s'affiche tout de suite et se déploie en direct
     (le calcul préalable est désactivé, la barre ne disparaîtrait jamais seule) */
  #loadingBar { display: none !important; }
  #mynetwork { border: 0 !important; }
  .vis-tooltip { max-width: 460px; white-space: normal !important; padding: 9px 11px !important;
                 font: 13px/1.5 Inter, system-ui, 'Segoe UI', sans-serif;
                 background: __BG__ !important; color: __TEXT__ !important;
                 border: 1px solid __BORDER__ !important; border-radius: 8px !important;
                 box-shadow: 0 6px 20px -12px rgba(20, 33, 61, .35); }
  #panacea-legend { position: fixed; top: 12px; left: 12px; z-index: 10; background: __BG__;
                    border: 1px solid __BORDER__; border-radius: 10px; padding: 10px 14px; color: __TEXT__;
                    font: 13px/1.6 Inter, system-ui, 'Segoe UI', sans-serif; max-width: 290px; }
  #panacea-legend h3 { margin: 0 0 6px; font-size: 14px; font-weight: 600; }
  #panacea-legend .sym { display: inline-block; width: 14px; text-align: center; margin-right: 6px; }
  #panacea-legend label { display: block; cursor: pointer; }
  #panacea-legend input { accent-color: __ACCENT__; }
  #panacea-legend small { color: __MUTED__; }
  #panacea-legend hr { border: 0; border-top: 1px solid __BORDER__; margin: 6px 0; }
</style>
<div id="panacea-legend">
  <h3>Panacea — graphe de connaissances</h3>
  __LEGEND__
  <hr>
  <label><input type="checkbox" id="pf-top"> Top __TOP__ seulement</label>
  <label><input type="checkbox" id="pf-cpd" checked> Afficher les composés</label>
  <hr><small>__STATS__<br>Survolez un nœud pour le détail ; molette pour zoomer.</small>
</div>
<script>
(function () {
  // Infobulles : vis-network affiche les chaînes en texte brut -> on les convertit en HTML.
  nodes.update(nodes.get().map(function (n) {
    var el = document.createElement("div"); el.innerHTML = n.title || ""; return {id: n.id, title: el};
  }));
  var topBox = document.getElementById("pf-top"), cpdBox = document.getElementById("pf-cpd");
  function apply() {
    nodes.update(nodes.get().map(function (n) {
      var hide = (topBox.checked && !n.in_top) || (!cpdBox.checked && n.kind === "compound");
      return {id: n.id, hidden: hide};
    }));
  }
  topBox.addEventListener("change", apply); cpdBox.addEventListener("change", apply);
})();
</script>
"""


def export_html(G: nx.Graph, path: Path) -> None:
    net = Network(height="100vh", width="100%", bgcolor=BACKGROUND, font_color=INK,
                  cdn_resources="in_line", select_menu=False, filter_menu=False)
    for n, d in G.nodes(data=True):
        st = STYLE[d["kind"]]
        face = "Inter, Segoe UI, sans-serif"
        if d["kind"] == "plant":
            size, font = 12 + d["score"] * 0.45, {"size": 19, "face": face, "bold": True,
                                                  "color": COLORS["accent_ink"]}
        elif d["kind"] == "indication":
            size, font = 6 + 4 * (G.degree(n) - 1), {"size": 11 + 2 * (G.degree(n) - 1), "face": face}
        else:
            size, font = 7, {"size": 10, "color": COLORS["muted"], "face": face}
        net.add_node(n, label=d["label"], title=tooltip(n, d, G), color=st["color"],
                     shape=st["shape"], size=size, font=font, kind=d["kind"], in_top=d["in_top"])
    for u, v, d in G.edges(data=True):
        color = GRAPH["edge_treats"] if d["kind"] == "treats" else GRAPH["edge_contains"]
        net.add_edge(u, v, color=color, width=1)

    net.set_options("""{
      "physics": {"solver": "forceAtlas2Based",
                  "forceAtlas2Based": {"gravitationalConstant": -60, "springLength": 90,
                                       "avoidOverlap": 0.4},
                  "stabilization": {"enabled": false}},
      "interaction": {"hover": true, "tooltipDelay": 120, "hideEdgesOnDrag": true}
    }""")

    kinds = defaultdict(int)
    for _, d in G.nodes(data=True):
        kinds[d["kind"]] += 1
    symbols = {"dot": "●", "diamond": "◆", "triangle": "▲"}
    legend = "".join(
        f'<div><span class="sym" style="color:{s["color"]}">{symbols[s["shape"]]}</span>'
        f'{s["label"]} — {kinds[k]}</div>' for k, s in STYLE.items())
    stats = f"{G.number_of_nodes()} nœuds, {G.number_of_edges()} liens"
    extra = (EXTRA_HTML.replace("__LEGEND__", legend).replace("__TOP__", str(TOP_FILTER))
             .replace("__STATS__", stats))
    for token, key in (("__BG__", "bg"), ("__TEXT__", "text"), ("__BORDER__", "border"),
                       ("__ACCENT__", "accent"), ("__MUTED__", "muted")):
        extra = extra.replace(token, COLORS[key])

    page = net.generate_html()
    page = page.replace("</body>", extra + "\n</body>", 1)
    page = page.replace("<head>", '<head>\n<meta charset="utf-8"><title>Panacea — graphe</title>', 1)
    path.write_text(page, encoding="utf-8")  # pyvis.write_html n'impose pas l'UTF-8 sous Windows


def main() -> None:
    parser = argparse.ArgumentParser(description="Graphe de connaissances Panacea")
    parser.add_argument("--top", type=int, default=None, help="n'exporter que les N meilleures plantes")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    G, _ = build_graph(args.top)
    export_html(G, args.output)

    count = defaultdict(int)
    for _, d in G.nodes(data=True):
        count[d["kind"]] += 1
    shared = sorted(((G.degree(n), d["label"], n) for n, d in G.nodes(data=True)
                     if d["kind"] == "indication"), reverse=True)[:5]
    exact = sum(q["exact"] for _, d in G.nodes(data=True) if d["kind"] == "indication"
                for q in d["quotes"])
    total_q = sum(len(d["quotes"]) for _, d in G.nodes(data=True) if d["kind"] == "indication")

    print("=" * 70)
    print(f"Nœuds : {G.number_of_nodes()}  (plantes {count['plant']}, indications "
          f"{count['indication']}, composés {count['compound']})")
    print(f"Liens : {G.number_of_edges()}")
    print(f"Citations précises par indication : {exact}/{total_q} "
          f"(sinon citation générale de la fiche)")
    print(f"Graphe -> {args.output}")
    print("=" * 70)
    print("\nIndications partagées par le plus de plantes :")
    for deg, label, n in shared:
        plants = sorted(G.nodes[nb]["label"] for nb in G.neighbors(n))
        print(f"  • {label} — {deg} plantes : {', '.join(plants)}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
