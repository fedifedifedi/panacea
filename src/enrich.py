"""Étape 3 : enrichissement chimique via Wikidata.

Pour chaque plante de data/extracted.json ayant un scientific_name :
1. trouve l'élément Wikidata du taxon (propriété P225 "taxon name") ;
2. récupère jusqu'à 8 composés chimiques "found in taxon" (P703), les plus
   caractéristiques de la plante (documentés ET peu répandus ailleurs, voir
   find_compounds), ainsi que le nombre total de composés connus pour ce taxon.

Attention : les données P703 de Wikidata contiennent des erreurs d'annotation
(ex. médicaments de synthèse rattachés à une plante). Les filtres en réduisent
une partie ; les composés restent des pistes à vérifier, pas des faits établis.

Sortie : data/enriched.json (champs ajoutés : wikidata_id, compounds, compounds_total)
Cache  : data/cache/wikidata.json (une entrée par requête SPARQL)

Usage : python src/enrich.py [--max-compounds 8] [--pause 1.5]
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INPUT_PATH = DATA_DIR / "extracted.json"
OUTPUT_PATH = DATA_DIR / "enriched.json"
CACHE_PATH = DATA_DIR / "cache" / "wikidata.json"

SPARQL_URL = "https://query.wikidata.org/sparql"
# Politique Wikimedia : User-Agent explicite identifiant l'outil et son usage.
HEADERS = {
    "User-Agent": "Panacea/0.1 (educational project analysing historical herbals; "
                  "python-requests) ",
    "Accept": "application/sparql-results+json",
}
DEFAULT_PAUSE = 1.5
MAX_RETRIES = 4

SPECIES_RANK = "Q7432"  # rang taxonomique "espèce"
MIN_TAXA = 2            # nombre minimal de taxons où un composé doit être signalé


# --------------------------------------------------------------------------- #
# Requêtes SPARQL avec cache
# --------------------------------------------------------------------------- #
class WikidataClient:
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

    def query(self, sparql: str) -> list[dict]:
        """Exécute une requête SPARQL (ou la lit dans le cache) ; renvoie les lignes."""
        key = hashlib.sha1(" ".join(sparql.split()).encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]

        for attempt in range(MAX_RETRIES + 1):
            if self.api_calls:
                time.sleep(self.pause)  # pause polie entre deux appels réels
            self.api_calls += 1
            try:
                resp = self.session.get(SPARQL_URL, params={"query": sparql}, timeout=60)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    wait = float(resp.headers.get("Retry-After", 5 * 2 ** attempt))
                    print(f"    ↻ Wikidata {resp.status_code}, nouvel essai dans {wait:.0f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                bindings = resp.json()["results"]["bindings"]
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= MAX_RETRIES:
                    raise
                print(f"    ↻ {type(e).__name__}, nouvel essai")
                time.sleep(5 * 2 ** attempt)

        # On ne garde que les valeurs simples (plus compact dans le cache).
        rows = [{k: v["value"] for k, v in b.items()} for b in bindings]
        self.cache[key] = rows
        self._save_cache()
        return rows


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def sparql_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
# Étapes d'enrichissement
# --------------------------------------------------------------------------- #
def find_taxon(client: WikidataClient, scientific_name: str) -> str | None:
    """Élément Wikidata du taxon : on préfère le rang 'espèce', puis le plus documenté."""
    rows = client.query(f"""
        SELECT ?item ?rank ?sitelinks WHERE {{
          ?item wdt:P225 {sparql_string(scientific_name)} .
          OPTIONAL {{ ?item wdt:P105 ?rank }}
          OPTIONAL {{ ?item wikibase:sitelinks ?sitelinks }}
        }}""")
    if not rows:
        return None
    best = max(rows, key=lambda r: (qid(r.get("rank", "")) == SPECIES_RANK,
                                    int(r.get("sitelinks", 0))))
    return qid(best["item"])


def find_compounds(client: WikidataClient, taxon_id: str, limit: int) -> tuple[list[dict], int]:
    """(composés les plus caractéristiques avec un nom anglais, nombre total de composés).

    Filtre : composé signalé dans au moins MIN_TAXA taxons, éléments chimiques exclus.
    Classement : score = sitelinks / nombre de taxons où le composé est présent.
    - sitelinks (pages Wikipédia liées) favorise les molécules documentées ;
    - diviser par le nombre de taxons écarte les composés ubiquitaires (sucres,
      acides aminés, acides gras) au profit des composés propres à la plante.
    """
    rows = client.query(f"""
        SELECT ?compound ?label ?sitelinks (COUNT(DISTINCT ?taxon) AS ?ntaxa) WHERE {{
          ?compound wdt:P703 wd:{taxon_id} ;
                    rdfs:label ?label ;
                    wikibase:sitelinks ?sitelinks ;
                    wdt:P703 ?taxon .
          FILTER(LANG(?label) = "en")
          FILTER(?sitelinks >= 1)
          FILTER NOT EXISTS {{ ?compound wdt:P31 wd:Q11344 }}  # pas d'éléments (zinc, fer…)
        }}
        GROUP BY ?compound ?label ?sitelinks""")
    # Un composé signalé dans un seul taxon est souvent une annotation erronée
    # (médicaments de synthèse, réactifs de laboratoire) : on exige >= 2 taxons.
    rows = [r for r in rows if int(r["ntaxa"]) >= MIN_TAXA]
    for r in rows:
        r["score"] = int(r["sitelinks"]) / max(int(r["ntaxa"]), 1)
    rows.sort(key=lambda r: (-r["score"], r["label"]))
    compounds = [
        {"name": r["label"], "wikidata_id": qid(r["compound"]),
         "found_in_taxa": int(r["ntaxa"]), "wikipedia_links": int(r["sitelinks"])}
        for r in rows[:limit]
    ]

    total_rows = client.query(f"""
        SELECT (COUNT(DISTINCT ?compound) AS ?n) WHERE {{ ?compound wdt:P703 wd:{taxon_id} }}""")
    total = int(total_rows[0]["n"]) if total_rows else 0
    return compounds, total


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Enrichissement chimique via Wikidata")
    parser.add_argument("--max-compounds", type=int, default=8)
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="pause entre appels (s)")
    args = parser.parse_args()

    plants = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    client = WikidataClient(pause=args.pause)
    enriched = []

    for n, plant in enumerate(plants, 1):
        record = {**plant, "wikidata_id": None, "compounds": [], "compounds_total": 0}
        sci = plant.get("scientific_name")
        label = f"[{n}/{len(plants)}] {plant['plant_name']}"
        if not sci:
            print(f"{label} — pas de nom scientifique, ignorée")
            enriched.append(record)
            continue
        try:
            taxon = find_taxon(client, sci)
            if taxon:
                compounds, total = find_compounds(client, taxon, args.max_compounds)
                record.update(wikidata_id=taxon, compounds=compounds, compounds_total=total)
                print(f"{label} ({sci}) -> {taxon}, {total} composés")
            else:
                print(f"{label} ({sci}) -> taxon introuvable dans Wikidata")
        except requests.RequestException as e:
            # Erreur réseau persistante : plante laissée sans enrichissement, on continue.
            print(f"{label} ({sci}) -> ✗ erreur Wikidata : {e}")
        enriched.append(record)

    OUTPUT_PATH.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    with_taxon = [r for r in enriched if r["wikidata_id"]]
    with_compounds = [r for r in enriched if r["compounds"]]
    top = sorted(with_compounds, key=lambda r: r["compounds_total"], reverse=True)[:3]

    print("\n" + "=" * 70)
    print(f"Plantes enrichies (taxon trouvé)   : {len(with_taxon)} / {len(enriched)}")
    print(f"  dont avec au moins un composé    : {len(with_compounds)}")
    print(f"Composés retenus (max {args.max_compounds}/plante)   : "
          f"{sum(len(r['compounds']) for r in enriched)}")
    print(f"Composés connus au total (Wikidata): {sum(r['compounds_total'] for r in enriched):,}")
    print(f"Appels réseau : {client.api_calls} (le reste vient du cache)")
    print(f"Résultats -> {OUTPUT_PATH}")
    print("=" * 70)
    print("\nPlantes les plus riches en composés connus :")
    for r in top:
        names = ", ".join(c["name"] for c in r["compounds"])
        print(f"  • {r['plant_name'].title()} ({r['scientific_name']}, {r['wikidata_id']}) : "
              f"{r['compounds_total']} composés\n    ex. {names}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
