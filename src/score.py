"""Étape 4 : score "piste innovante".

Pour chaque plante de data/enriched.json :
1. compte les articles PubMed mentionnant son nom scientifique (total et depuis 2016)
   via l'API E-utilities (esearch) ;
2. calcule un score combinant usage traditionnel, chimie connue et faible
   couverture scientifique ;
3. enregistre le classement dans data/leads.json.

Cache : data/cache/pubmed.json (une entrée par requête)
Usage : python src/score.py [--pause 0.5]
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INPUT_PATH = DATA_DIR / "enriched.json"
PLANTS_PATH = DATA_DIR / "plants.json"
OUTPUT_PATH = DATA_DIR / "leads.json"
CACHE_PATH = DATA_DIR / "cache" / "pubmed.json"

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
HEADERS = {"User-Agent": "Panacea/0.1 (educational project analysing historical herbals; "
                         "python-requests)"}
TOOL_NAME = "panacea"     # paramètre "tool" recommandé par le NCBI
RECENT_YEAR = 2016
DEFAULT_PAUSE = 0.5       # NCBI : max 3 requêtes/s sans clé API
MAX_RETRIES = 4

# =========================================================================== #
# FORMULE DU SCORE "PISTE INNOVANTE"
# =========================================================================== #
# Idée : une bonne piste est une plante dont l'usage traditionnel est riche,
# dont la chimie offre de la matière à explorer, mais que la recherche moderne
# a encore peu étudiée.
#
# Trois critères, chacun ramené entre 0 et 1 par normalisation min-max sur
# l'ensemble des plantes classées  (x - min) / (max - min) :
#
#   tradition  = norm( nb d'indications chez Culpeper )
#                Échelle linéaire : 20 indications valent vraiment plus que 10.
#
#   chimie     = norm( log10(1 + compounds_total) )
#                Échelle log : passer de 0 à 40 composés connus compte bien plus
#                que de 400 à 440 (les grosses listes sont souvent du bruit).
#
#   nouveauté  = 1 - norm( log10(1 + articles PubMed) )
#                Échelle log : 20 -> 200 articles est un saut comparable à
#                200 -> 2000. Inversée : moins d'articles = critère plus élevé.
#
# Score final (entre 0 et 1, affiché sur 100) :
#
#   score = 0.50 * nouveauté + 0.30 * tradition + 0.20 * chimie
#
# Pondérations :
#   - nouveauté 50 % : c'est le cœur de la notion de piste "innovante" ;
#   - tradition 30 % : un usage ancien riche est l'indice que la plante agit ;
#   - chimie    20 % : utile mais moins fiable. Les données Wikidata sont bruitées
#                      et surtout corrélées à l'effort de recherche : une plante
#                      très étudiée a mécaniquement beaucoup de composés connus.
#                      Un poids plus fort récompenserait les plantes déjà étudiées
#                      et contredirait le critère de nouveauté (essai à 40/35/25 :
#                      Sage et Rosemary, >1000 articles, se retrouvaient dans le top 5).
#
# Limites : le nombre d'indications dépend aussi de la longueur du chapitre et
# de l'extracteur (Gemini ou Claude) ; le comptage PubMed par nom latin ignore
# les synonymes (ex. Rosmarinus officinalis / Salvia rosmarinus).
# =========================================================================== #
WEIGHTS = {"novelty": 0.50, "tradition": 0.30, "chemistry": 0.20}


# --------------------------------------------------------------------------- #
# PubMed avec cache
# --------------------------------------------------------------------------- #
class PubMedClient:
    def __init__(self, pause: float):
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.cache = self._load_cache()
        self.api_calls = 0

    @staticmethod
    def _load_cache() -> dict:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"⚠ Cache illisible, ignoré : {CACHE_PATH}")
        return {}

    def _save_cache(self) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE_PATH)

    def count(self, term: str, since_year: int | None = None) -> int:
        """Nombre d'articles PubMed pour `term` (optionnellement depuis `since_year`)."""
        key = f"{term}|since={since_year or ''}"
        if key in self.cache:
            return self.cache[key]

        params = {"db": "pubmed", "term": term, "retmax": 0, "retmode": "json", "tool": TOOL_NAME}
        if since_year:
            params.update(datetype="pdat", mindate=str(since_year), maxdate="3000")

        for attempt in range(MAX_RETRIES + 1):
            if self.api_calls:
                time.sleep(self.pause)
            self.api_calls += 1
            try:
                resp = self.session.get(ESEARCH_URL, params=params, timeout=30)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                result = resp.json()["esearchresult"]
                if "ERROR" in result or "count" not in result:
                    raise ValueError(f"réponse PubMed inattendue : {result}")
                n = int(result["count"])
                break
            except (requests.RequestException, ValueError, KeyError) as e:
                if attempt >= MAX_RETRIES:
                    raise
                wait = 2 * 2 ** attempt
                print(f"    ↻ PubMed : {e} — nouvel essai dans {wait}s")
                time.sleep(wait)

        self.cache[key] = n
        self._save_cache()
        return n


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #
def min_max(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]  # critère non discriminant
    return [(v - lo) / (hi - lo) for v in values]


def level(x: float) -> int:
    """0 = bas, 1 = moyen, 2 = haut (tiers de l'échelle normalisée)."""
    return 0 if x < 1 / 3 else (1 if x < 2 / 3 else 2)


def explain(n_ind: int, n_comp: int, n_pub: int, c: dict) -> str:
    """Phrase courte en français résumant les trois critères."""
    trad = ["Usage ancien limité", "Usage ancien modéré", "Usage ancien riche"][level(c["tradition"])]
    chem = ["chimie peu connue", "chimie assez documentée", "chimie bien documentée"][level(c["chemistry"])]
    study = ["déjà très étudiée", "moyennement étudiée", "peu étudiée"][level(c["novelty"])]
    plural = lambda n, w: f"{n} {w}{'s' if n > 1 else ''}"
    # "mais" quand la recherche contredit l'intérêt de la plante, "et" sinon
    link = "mais" if (level(c["tradition"]) >= 1) != (level(c["novelty"]) >= 1) else "et"
    return (f"{trad} ({plural(n_ind, 'indication')}), {chem} ({plural(n_comp, 'composé')}), "
            f"{link} {study} ({plural(n_pub, 'article')} PubMed).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score 'piste innovante' avec PubMed")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="pause entre appels (s)")
    args = parser.parse_args()

    plants = [p for p in json.loads(INPUT_PATH.read_text(encoding="utf-8")) if p.get("scientific_name")]
    names = {p["id"]: p["name"] for p in json.loads(PLANTS_PATH.read_text(encoding="utf-8"))}
    client = PubMedClient(pause=args.pause)

    rows = []
    for n, p in enumerate(plants, 1):
        sci = p["scientific_name"]
        term = f'"{sci}"'  # nom exact entre guillemets, tous champs
        try:
            total = client.count(term)
            recent = client.count(term, since_year=RECENT_YEAR)
        except Exception as e:  # on n'invente pas de chiffre : plante exclue du classement
            print(f"[{n}/{len(plants)}] {sci} -> ✗ PubMed indisponible ({e}), plante ignorée")
            continue
        print(f"[{n}/{len(plants)}] {sci} -> {total} articles ({recent} depuis {RECENT_YEAR})")
        rows.append({
            "id": p["id"],
            "name": names.get(p["id"], p["plant_name"].title()),
            "scientific_name": sci,
            "wikidata_id": p.get("wikidata_id"),
            "n_indications": len(p.get("indications") or []),
            "compounds_total": p.get("compounds_total", 0),
            "pubmed_total": total,
            "pubmed_since_2016": recent,
            "extracted_by": p.get("extracted_by"),
        })

    if not rows:
        sys.exit("Aucune plante n'a pu être évaluée.")

    tradition = min_max([r["n_indications"] for r in rows])
    chemistry = min_max([math.log10(1 + r["compounds_total"]) for r in rows])
    studied = min_max([math.log10(1 + r["pubmed_total"]) for r in rows])

    leads = []
    for r, t, c, s in zip(rows, tradition, chemistry, studied):
        criteria = {"tradition": round(t, 3), "chemistry": round(c, 3), "novelty": round(1 - s, 3)}
        score = sum(WEIGHTS[k] * criteria[k] for k in WEIGHTS)
        leads.append({
            "name": r["name"],
            "scientific_name": r["scientific_name"],
            "score": round(100 * score, 1),
            "criteria": criteria,
            "raw": {
                "n_indications": r["n_indications"],
                "compounds_total": r["compounds_total"],
                "pubmed_total": r["pubmed_total"],
                "pubmed_since_2016": r["pubmed_since_2016"],
                "recent_share": round(r["pubmed_since_2016"] / r["pubmed_total"], 2)
                                if r["pubmed_total"] else None,
            },
            "explanation": explain(r["n_indications"], r["compounds_total"], r["pubmed_total"], criteria),
            "id": r["id"],
            "wikidata_id": r["wikidata_id"],
            "extracted_by": r["extracted_by"],
        })

    leads.sort(key=lambda x: x["score"], reverse=True)
    for rank, lead in enumerate(leads, 1):
        lead["rank"] = rank
    output = {
        "formula": "score = 100 * (0.50*novelty + 0.30*tradition + 0.20*chemistry)",
        "weights": WEIGHTS,
        "notes": "Critères normalisés min-max sur les plantes classées ; "
                 "novelty = 1 - norm(log10(1+articles)), chemistry = norm(log10(1+composés)).",
        "leads": leads,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    def show(lead: dict) -> None:
        print(f"  {lead['rank']:>2}. {lead['name']} ({lead['scientific_name']}) — score {lead['score']}")
        print(f"      {lead['explanation']}")

    print("\n" + "=" * 70)
    print(f"{len(leads)} plantes classées -> {OUTPUT_PATH}  (appels PubMed : {client.api_calls})")
    print("=" * 70)
    print("\nTOP 5 — pistes les plus innovantes :")
    for lead in leads[:5]:
        show(lead)
    print("\nBAS DU CLASSEMENT — pistes les moins innovantes :")
    for lead in leads[-3:]:
        show(lead)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
