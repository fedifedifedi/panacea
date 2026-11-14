"""Étape 6 : assistant questions-réponses (RAG) croisant trois sources.

Sources
  1. Culpeper, The Complete Herbal (data/plants.json), en morceaux d'environ
     500 caractères avec chevauchement, chaque morceau gardant le nom de la plante ;
  2. PubMed : 3 à 5 articles récents avec résumé par plante de data/leads.json
     (esearch puis efetch), mis en cache dans data/pubmed_abstracts.json ;
  3. les fiches structurées de data/enriched.json + data/leads.json
     (indications, composés, score de piste).

Recherche hybride
  BM25 (rank_bm25, mots-clés) + embeddings locaux multilingues
  (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), fusionnés par
  Reciprocal Rank Fusion. L'index est construit une fois dans data/index/ et
  reconstruit automatiquement seulement si une source change.

Réponse
  Gemini (mêmes modèles de secours que extract.py), en français, avec une partie
  "Savoir ancien" et une partie "Science moderne", sources numérotées.
  Mode de secours : si Gemini est indisponible, affiche les 5 passages les plus
  pertinents et leurs sources.

Usage
  python src/rag.py "Quelles plantes contre la jaunisse ?"
  python src/rag.py "..." --no-llm      # recherche seule, sans appeler Gemini
  python src/rag.py --build             # (re)construire l'index uniquement
"""

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import numpy as np
import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.score import HEADERS, TOOL_NAME  # même User-Agent / identifiant NCBI que l'étape 4

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PLANTS_PATH = DATA_DIR / "plants.json"
ENRICHED_PATH = DATA_DIR / "enriched.json"
LEADS_PATH = DATA_DIR / "leads.json"
ABSTRACTS_PATH = DATA_DIR / "pubmed_abstracts.json"
INDEX_DIR = DATA_DIR / "index"

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Copie locale du modèle (téléchargée une fois ; évite tout accès réseau ensuite)
LOCAL_MODEL_DIR = ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 500        # caractères ; le modèle lit ~128 tokens, soit ~500 caractères
CHUNK_OVERLAP = 80
ARTICLES_PER_PLANT = 5
PUBMED_PAUSE = 0.4      # NCBI : max 3 requêtes/s sans clé
RRF_K = 60              # constante classique de la Reciprocal Rank Fusion
CANDIDATES = 50         # résultats gardés par chaque méthode avant fusion
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Nombre de passages envoyés à Gemini, par source (garantit les deux parties de la réponse)
CONTEXT_QUOTA = {"culpeper": 8, "pubmed": 6, "fiche": 4}
FALLBACK_TOP = 5

SOURCE_LABEL = {"culpeper": "Culpeper (1653)", "pubmed": "PubMed", "fiche": "Fiche Panacea"}


# =========================================================================== #
# 1. PubMed : esearch + efetch, avec cache
# =========================================================================== #
def _get(session: requests.Session, url: str, params: dict, retries: int = 4) -> requests.Response:
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params={**params, "tool": TOOL_NAME}, timeout=60)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt >= retries:
                raise
            wait = 2 * 2 ** attempt
            print(f"    ↻ PubMed : {e} — nouvel essai dans {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _text(el: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def parse_efetch(xml_text: str) -> list[dict]:
    """Extrait titre, année, résumé et PMID du XML PubMed."""
    articles = []
    for art in ET.fromstring(xml_text).iter("PubmedArticle"):
        pmid = _text(art.find("MedlineCitation/PMID"))
        a = art.find("MedlineCitation/Article")
        if a is None or not pmid:
            continue
        parts = []
        for ab in a.findall("Abstract/AbstractText"):
            label = ab.get("Label")
            parts.append(f"{label}: {_text(ab)}" if label else _text(ab))
        year = (_text(a.find("Journal/JournalIssue/PubDate/Year"))
                or _text(a.find("ArticleDate/Year"))
                or _text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4])
        abstract = " ".join(p for p in parts if p)
        if not abstract:
            continue
        # Article non anglophone : PubMed met "[Not Available]." et le titre original
        # dans VernacularTitle.
        title = _text(a.find("ArticleTitle"))
        if not title or title.startswith("[Not Available]"):
            vernacular = _text(a.find("VernacularTitle"))
            lang = _text(a.find("Language"))
            title = f"{vernacular} [{lang}]" if vernacular else f"(titre non disponible) [{lang}]"
        articles.append({
            "pmid": pmid,
            "title": title,
            "year": int(year) if year[:4].isdigit() else None,
            "journal": _text(a.find("Journal/Title")),
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return articles


def fetch_abstracts() -> dict:
    """Pour chaque plante classée : les articles récents avec résumé (cache par nom latin)."""
    leads = json.loads(LEADS_PATH.read_text(encoding="utf-8"))["leads"]
    cache = json.loads(ABSTRACTS_PATH.read_text(encoding="utf-8")) if ABSTRACTS_PATH.exists() else {}
    todo = [l for l in leads if l["scientific_name"] not in cache]
    if not todo:
        return cache

    print(f"PubMed : récupération des résumés pour {len(todo)} plante(s)…")
    session = requests.Session()
    session.headers.update(HEADERS)
    for n, lead in enumerate(todo, 1):
        sci = lead["scientific_name"]
        try:
            ids = _get(session, f"{EUTILS}/esearch.fcgi", {
                "db": "pubmed", "term": f'"{sci}"[tiab] AND hasabstract',
                "sort": "pub_date", "retmax": ARTICLES_PER_PLANT, "retmode": "json",
            }).json()["esearchresult"].get("idlist", [])
            time.sleep(PUBMED_PAUSE)
            articles = []
            if ids:
                xml_text = _get(session, f"{EUTILS}/efetch.fcgi", {
                    "db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml",
                }).text
                articles = parse_efetch(xml_text)
                time.sleep(PUBMED_PAUSE)
        except (requests.RequestException, ET.ParseError, KeyError, ValueError) as e:
            print(f"  [{n}/{len(todo)}] {sci} -> ✗ {e} (sera retenté au prochain lancement)")
            continue
        cache[sci] = {"plant": lead["name"], "fetched": date.today().isoformat(), "articles": articles}
        ABSTRACTS_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [{n}/{len(todo)}] {sci} -> {len(articles)} article(s)")
    return cache


# =========================================================================== #
# 1 bis. PubMed ciblé au moment de la question : "nom scientifique AND indication"
# =========================================================================== #
TARGETED_CACHE_PATH = DATA_DIR / "cache" / "pubmed_targeted.json"
TARGETED_MAX_ARTICLES = 3
TARGETED_MAX_PLANTS = 3
TARGETED_ABSTRACT_CHARS = 1500   # longueur du résumé transmise à Gemini


class PlantLookup:
    """Repère les plantes citées dans une question (noms de Culpeper, synonymes, nom latin).

    Tolère les variantes d'orthographe : « garlic » -> « Garlick », « camomille » ->
    « Camomile » (similarité de chaînes >= 0.85 sur les mots d'au moins 4 lettres).
    """

    def __init__(self):
        from difflib import SequenceMatcher
        self._ratio = lambda a, b: SequenceMatcher(None, a, b).ratio()
        sci = {p["id"]: p.get("scientific_name") for p in
               json.loads(ENRICHED_PATH.read_text(encoding="utf-8"))}
        self.entries = []  # (forme normalisée, nb de mots, plante)
        self.by_id: dict[int, dict] = {}
        self.by_name: dict[str, dict] = {}
        for p in json.loads(PLANTS_PATH.read_text(encoding="utf-8")):
            plant = {"id": p["id"], "name": p["name"], "scientific_name": sci.get(p["id"])}
            self.by_id[p["id"]] = plant
            self.by_name.setdefault(p["name"], plant)
            forms = [p["name"], *p["aliases"]] + ([plant["scientific_name"]] if plant["scientific_name"] else [])
            for form in forms:
                norm = strip_accents(form.lower().replace("’", "'"))
                norm = re.sub(r"[^a-z' ]+", " ", norm).strip()
                if len(norm) >= 4:
                    self.entries.append((norm, len(norm.split()), plant))

    def find(self, query: str) -> tuple[list[dict], set[str]]:
        """(plantes trouvées, mots de la question qui désignent ces plantes)."""
        q = re.sub(r"[^a-z' -]+", " ", strip_accents(query.lower().replace("’", "'")))
        found, used = {}, set()
        # 1) Noms français courants (« l'ail », « grande camomille »), les plus longs d'abord
        padded = " " + re.sub(r"\b[ld]'", " ", q) + " "
        for fr, culpeper_name in sorted(FR_PLANT_NAMES.items(), key=lambda kv: -len(kv[0])):
            fr_key = strip_accents(fr)
            if f" {fr_key} " in padded:
                plant = self.by_name.get(culpeper_name)
                if plant:
                    found.setdefault(plant["id"], {**plant, "query_words": []})
                    used.update(tokenize(fr_key))
                    padded = padded.replace(f" {fr_key} ", " ")  # « camomille » déjà consommé
        q = padded.strip()
        # 2) Noms anglais de Culpeper, synonymes et noms latins (tolérance orthographique)
        words = [w for w in q.split() if len(w) >= 4]
        for norm, n_words, plant in self.entries:
            if n_words > 1:  # nom composé : présence exacte dans la question
                if f" {norm} " in f" {q} ":
                    found.setdefault(plant["id"], {**plant, "query_words": []})
                    used.update(tokenize(norm))  # même découpage que indication_terms
                continue
            for w in words:
                if w not in STOPWORDS and self._ratio(w, norm) >= 0.85:
                    entry = found.setdefault(plant["id"], {**plant, "query_words": []})
                    if w not in entry["query_words"]:
                        entry["query_words"].append(w)  # nom tel que tapé : « garlic »
                    used.update(tokenize(w))
        # Préfère les plantes avec un nom scientifique (seules interrogeables dans PubMed)
        plants = sorted(found.values(), key=lambda p: p["scientific_name"] is None)
        return plants, used


def indication_terms(query: str, plant_words: set[str], expansion_modern: list[str]) -> list[str]:
    """Termes d'indication en anglais pour PubMed.

    - question en français : mots traduits par le glossaire (+ expansion) ;
    - sinon : mots restants de la question (hors mots vides et noms de plantes)
      + les termes d'expansion trouvés dans le vocabulaire des indications.
    """
    words = [w for w in tokenize(query) if w not in plant_words
             and strip_accents(w) not in plant_words]
    # Traduction sur la question d'origine : les expressions (« maux de tête ») gardent
    # leurs mots outils ; les noms de plantes ne figurent pas dans le glossaire médical.
    translated = translate_query(query)
    base = translated if translated else words
    terms = []
    for t in [*base, *expansion_modern[:2]]:
        t = t.strip().lower()
        if t and t not in terms:
            terms.append(t)
    return terms[:4]


# Synonymes usuels côté PubMed : la littérature emploie souvent un autre terme
# que le mot de la question (« feverfew » est étudié pour la « migraine »).
PUBMED_SYNONYMS = {
    "headache": ["migraine"], "migraine": ["headache"],
    "infection": ["antibacterial", "antimicrobial"], "infections": ["antibacterial", "antimicrobial"],
    "wound": ["wound healing"], "wounds": ["wound healing"],
    "cough": ["antitussive"], "insomnia": ["sleep"], "sleep": ["insomnia"],
    "melancholy": ["depression"], "depression": ["antidepressant"],
    "anxiety": ["anxiolytic"], "fever": ["antipyretic"], "pain": ["analgesic"],
    "inflammation": ["anti-inflammatory"], "jaundice": ["hepatoprotective", "liver"],
    "epilepsy": ["anticonvulsant", "seizures"], "worms": ["anthelmintic"],
    "gout": ["uric acid"], "stones": ["urolithiasis"], "digestion": ["dyspepsia"],
}


def pubmed_query(sci: str, terms: list[str], common_names: list[str] | None = None) -> str:
    """'("Nom latin"[tiab] OR nom courant[tiab]) AND (terme OR synonyme…) AND hasabstract'."""
    expanded = []
    for t in terms:
        for x in [t, *PUBMED_SYNONYMS.get(t, [])]:
            if x not in expanded:
                expanded.append(x)
    alt = " OR ".join(f'"{t}"' if " " in t else t for t in expanded[:6])
    names = [f'"{sci}"[tiab]'] + [f"{n}[tiab]" for n in (common_names or [])]
    plant = names[0] if len(names) == 1 else "(" + " OR ".join(names) + ")"
    return f"{plant} AND ({alt}) AND hasabstract"


def targeted_pubmed(plants: list[dict], terms: list[str]) -> list[dict]:
    """Interroge PubMed pour chaque plante x indication ; résultats mis en cache.

    Renvoie [{plant, scientific_name, query, articles, error}] ; une liste d'articles
    vide signifie "aucun article trouvé" (également mise en cache).
    """
    cache = json.loads(TARGETED_CACHE_PATH.read_text(encoding="utf-8")) \
        if TARGETED_CACHE_PATH.exists() else {}
    session = None
    out = []
    for plant in plants[:TARGETED_MAX_PLANTS]:
        sci = plant.get("scientific_name")
        if not sci:
            out.append({"plant": plant["name"], "scientific_name": None, "query": None,
                        "articles": [], "error": "nom scientifique inconnu (plante non extraite)"})
            continue
        # Noms courants : mots de la question désignant la plante (« garlic »), hors nom latin
        common = [w for w in plant.get("query_words", []) if w not in sci.lower()]
        query = pubmed_query(sci, terms, common)
        if query not in cache:
            if session is None:
                session = requests.Session()
                session.headers.update(HEADERS)
            try:
                # On demande un peu plus d'identifiants : certains résultats (chapitres de
                # livres, notices sans résumé exploitable) sont écartés par parse_efetch.
                ids = _get(session, f"{EUTILS}/esearch.fcgi", {
                    "db": "pubmed", "term": query, "sort": "relevance",
                    "retmax": TARGETED_MAX_ARTICLES + 2, "retmode": "json",
                }).json()["esearchresult"].get("idlist", [])
                time.sleep(PUBMED_PAUSE)
                articles = []
                if ids:
                    articles = parse_efetch(_get(session, f"{EUTILS}/efetch.fcgi", {
                        "db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml",
                    }).text)
                    order = {pmid: i for i, pmid in enumerate(ids)}  # garde l'ordre de pertinence
                    articles.sort(key=lambda a: order.get(a["pmid"], 99))
                    articles = articles[:TARGETED_MAX_ARTICLES]
                    time.sleep(PUBMED_PAUSE)
            except (requests.RequestException, ET.ParseError, KeyError, ValueError) as e:
                out.append({"plant": plant["name"], "scientific_name": sci, "query": query,
                            "articles": [], "error": f"PubMed indisponible ({e})"})
                continue  # pas de mise en cache d'un échec
            cache[query] = {"fetched": date.today().isoformat(), "articles": articles}
            TARGETED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            TARGETED_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        out.append({"plant": plant["name"], "scientific_name": sci, "query": query,
                    "articles": cache[query]["articles"], "error": None})
    return out


def targeted_docs(targeted: list[dict]) -> list[dict]:
    """Articles ciblés au format des passages (source "pubmed", marqués targeted)."""
    docs = []
    for t in targeted:
        for a in t["articles"]:
            body = f"{a['title']} {a['abstract']}"
            if len(body) > TARGETED_ABSTRACT_CHARS:
                body = body[:TARGETED_ABSTRACT_CHARS].rsplit(" ", 1)[0] + "…"
            docs.append({"source": "pubmed", "targeted": True, "plant": t["plant"],
                         "scientific_name": t["scientific_name"], "pmid": a["pmid"],
                         "title": a["title"], "year": a["year"], "url": a["url"], "text": body})
    return docs


# =========================================================================== #
# 2. Découpage des documents
# =========================================================================== #
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Fenêtres d'environ `size` caractères, coupées sur un espace, avec chevauchement."""
    text = re.sub(r"\s+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            end = cut if cut > 0 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        nxt = text.rfind(" ", start, end - overlap)
        start = nxt + 1 if nxt > start else end - overlap
    return [c for c in chunks if c]


def build_documents(abstracts: dict) -> list[dict]:
    docs = []
    # --- Culpeper
    for p in json.loads(PLANTS_PATH.read_text(encoding="utf-8")):
        for i, chunk in enumerate(chunk_text(p["text"])):
            docs.append({"source": "culpeper", "plant": p["name"], "plant_id": p["id"],
                         "chunk": i, "text": chunk})
    # --- PubMed
    for sci, entry in abstracts.items():
        for art in entry["articles"]:
            body = f"{art['title']} {art['abstract']}"
            for i, chunk in enumerate(chunk_text(body)):
                docs.append({"source": "pubmed", "plant": entry["plant"], "scientific_name": sci,
                             "pmid": art["pmid"], "title": art["title"], "year": art["year"],
                             "url": art["url"], "chunk": i, "text": chunk})
    # --- Fiches structurées
    leads = {l["id"]: l for l in json.loads(LEADS_PATH.read_text(encoding="utf-8"))["leads"]}
    for p in json.loads(ENRICHED_PATH.read_text(encoding="utf-8")):
        lead = leads.get(p["id"])
        name = lead["name"] if lead else p["plant_name"].title()
        parts = [f"Fiche {name} ({p.get('scientific_name') or 'nom latin inconnu'})."]
        if p.get("indications"):
            parts.append("Indications chez Culpeper : " + "; ".join(p["indications"]) + ".")
        if p.get("compounds"):
            parts.append("Composés connus (Wikidata) : "
                         + ", ".join(c["name"] for c in p["compounds"])
                         + f" ({p.get('compounds_total', 0)} au total).")
        if lead:
            parts.append(f"Score de piste innovante : {lead['score']}/100, rang {lead['rank']}. "
                         f"{lead['explanation']}")
        for i, chunk in enumerate(chunk_text(" ".join(parts))):
            docs.append({"source": "fiche", "plant": name, "plant_id": p["id"],
                         "scientific_name": p.get("scientific_name"), "chunk": i, "text": chunk})
    return docs


# =========================================================================== #
# 3. Index hybride (BM25 + embeddings), persisté dans data/index/
# =========================================================================== #
STOPWORDS = set("""
a an and are as at be but by for from had has have he her his i in is it its me my no not of on or
our she so that the their them then there these they this to was we were what which who will with
you your hath doth thereof therein thereby also being any all may such those than
le la les un une des du de d l et ou en au aux est sont pour par sur dans que qui quoi quelles
quels quel quelle ce cette ces se sa son ses leur leurs il elle ils elles on ne pas plus contre
plant plants herb herbs used use using against good helps help which were
plante plantes herbe herbes utilisé utilisée utilisées utilisés soigner traiter
""".split())  # dernières lignes : mots trop génériques dans ce corpus pour discriminer

# Expansion de requête par le vocabulaire des indications (étape 2) :
# "jaunisse" -> "jaundice" (+ terme ancien "yellow jaundice"), "epilepsy" -> "falling-sickness".
EXPANSION_MIN_SIM = 0.55   # similarité cosinus minimale entre la requête et un terme d'indication
EXPANSION_MARGIN = 0.08    # on ne garde que les termes presque aussi proches que le meilleur
EXPANSION_MAX_TERMS = 3

# Petit glossaire français -> anglais des termes médicaux courants. Le modèle
# multilingue léger rapproche mal certains mots rares (« jaunisse » ≠ « jaundice »).
# Clés sans accents ; une question en anglais n'est pas affectée.
GLOSSARY_FR_EN = {
    "jaunisse": "jaundice", "ictere": "jaundice", "epilepsie": "epilepsy", "goutte": "gout",
    "toux": "cough", "fievre": "fever", "fievres": "fever", "calcul": "stones", "calculs": "stones",
    "rein": "kidney", "reins": "kidney", "foie": "liver", "rate": "spleen", "vessie": "bladder",
    "plaie": "wound", "plaies": "wounds", "blessure": "wound", "blessures": "wounds",
    "brulure": "burns", "brulures": "burns", "ulcere": "ulcers", "ulceres": "ulcers",
    "migraine": "headache", "cephalee": "headache", "tete": "head", "insomnie": "insomnia",
    "sommeil": "sleep", "dents": "toothache", "dentaire": "toothache", "rage": "mad dog bite",
    "morsure": "bites", "morsures": "bites", "venin": "poison", "poison": "poison",
    "poisons": "poison", "vers": "worms", "diarrhee": "diarrhea", "dysenterie": "dysentery",
    "constipation": "constipation", "vomissements": "vomiting", "nausee": "nausea",
    "colique": "colic", "coliques": "colic", "flatulences": "flatulence", "gaz": "flatulence",
    "digestion": "digestion", "estomac": "stomach", "poumons": "lungs", "poumon": "lungs",
    "tuberculose": "consumption", "phtisie": "consumption", "asthme": "shortness of breath",
    "essoufflement": "shortness of breath", "respiration": "breath", "rhume": "cold",
    "catarrhe": "catarrh", "gorge": "throat", "enrouement": "hoarseness", "yeux": "eyes",
    "oeil": "eyes", "vue": "eyesight", "oreilles": "ears", "surdite": "deafness",
    "regles": "menstruation", "menstruations": "menstruation", "accouchement": "childbirth",
    "placenta": "after-birth", "lait": "breast milk", "seins": "breasts", "uterus": "womb",
    "melancolie": "melancholy", "depression": "melancholy", "tristesse": "melancholy",
    "anxiete": "anxiety", "evanouissement": "fainting", "vertige": "vertigo",
    "vertiges": "vertigo", "paralysie": "palsy", "convulsions": "convulsions",
    "crampes": "cramps", "rhumatismes": "rheumatism", "articulations": "joint pain",
    "sciatique": "sciatica", "hydropisie": "dropsy", "oedeme": "dropsy", "scorbut": "scurvy",
    "peste": "plague", "variole": "smallpox", "galle": "itch", "demangeaisons": "itch",
    "verrues": "warts", "taches": "spots", "hemorragie": "bleeding", "saignement": "bleeding",
    "saignements": "bleeding", "hemorroides": "piles", "urine": "urine", "urinaire": "urine",
    "inflammation": "inflammation", "inflammations": "inflammation", "enflure": "swelling",
    "gonflement": "swelling", "abces": "abscess", "fractures": "broken bones",
    "os": "bones", "hernie": "rupture", "coeur": "heart", "palpitations": "heart",
    "douleur": "pain", "douleurs": "pain", "cancer": "cancer", "tumeur": "tumour",
}


GLOSSARY_FR_EN.update({"infection": "infection", "infections": "infections",
                       "infectieux": "infection", "bacteries": "antibacterial"})

# Expressions de plusieurs mots (cherchées avant les mots isolés), sans accents.
PHRASES_FR_EN = {
    "maux de tete": "headache", "mal de tete": "headache", "mal de dents": "toothache",
    "maux de ventre": "colic", "mal de ventre": "colic", "mal de gorge": "sore throat",
    "calculs renaux": "kidney stones", "piqures d'insectes": "stings", "chute de cheveux": "hair loss",
    "regles douloureuses": "menstrual pain", "troubles du sommeil": "insomnia",
}

# Noms français courants -> nom du chapitre chez Culpeper (data/plants.json).
# Les plus longs d'abord : « grande camomille » (Feverfew) avant « camomille ».
FR_PLANT_NAMES = {
    "grande camomille": "Feverfew", "camomille romaine": "Camomile", "camomille": "Camomile",
    "ail": "Garlick", "sauge": "Sage", "romarin": "Rosemary", "lavande": "Lavender",
    "absinthe": "Wormwood", "achillee": "Yarrow", "millefeuille": "Yarrow",
    "valeriane": "Garden Valerian", "menthe": "Mint", "fenouil": "Fennel", "thym": "Thyme",
    "aigremoine": "Agrimony", "digitale": "Fox-Glove", "pavot": "Poppy", "coquelicot": "Poppy",
    "pissenlit": "Dandelion", "consoude": "Comfrey", "reglisse": "Liquorice", "melisse": "Balm",
    "millepertuis": "St. John’s Wort", "orcanette": "Alkanet", "douce-amere": "Amara Dulcis",
    "douce amere": "Amara Dulcis", "ortie": "Nettles", "orties": "Nettles", "plantain": "Plantain",
    "bourrache": "Borage and Bugloss", "hysope": "Hyssop", "persil": "Parsley",
    "chelidoine": "Celandine", "guimauve": "Mallows and Marshmallows", "mauve": "Mallows and Marshmallows",
    "souci": "Marigolds", "verveine": "Vervain", "tanaisie": "Garden Tansy", "marrube": "Horehound",
    "angelique": "Angelica", "bardane": "Burdock", "sureau": "Elder Tree", "jusquiame": "Henbane",
    "cigue": "Hemlock", "genievre": "Juniper Bush", "livèche": "Lovage", "liveche": "Lovage",
    "pivoine": "Male and Female Peony", "rue": "Garden-Rue", "oseille": "Sorrel",
    "chicoree": "Succory", "molene": "Mullein", "bouillon-blanc": "Mullein", "prele": "Horsetail",
    "houblon": "Hops", "gentiane": "Gentian", "angelique officinale": "Angelica",
}


def strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def translate_terms(words: list[str]) -> list[str]:
    """Traduit les mots français reconnus du glossaire (les autres sont ignorés)."""
    return [GLOSSARY_FR_EN[w] for w in (strip_accents(x) for x in words) if w in GLOSSARY_FR_EN]


def translate_query(text: str) -> list[str]:
    """Traduction FR -> EN des termes médicaux d'une question : expressions puis mots."""
    norm = " " + re.sub(r"[^a-z' -]+", " ", strip_accents(text.lower().replace("’", "'"))) + " "
    found = []
    for phrase, en in sorted(PHRASES_FR_EN.items(), key=lambda kv: -len(kv[0])):
        if f" {phrase} " in norm:
            found.append(en)
            norm = norm.replace(f" {phrase} ", " ")
    found += translate_terms(tokenize(norm))
    return list(dict.fromkeys(found))


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zà-öø-ÿ0-9]+", text.lower().replace("’", "'"))
    return [w for w in words if len(w) > 1 and w not in STOPWORDS]


def embed_input(doc: dict) -> str:
    """Texte vectorisé : le nom de la plante est préfixé pour garder le contexte."""
    return f"{doc['plant']}: {doc['text']}"


def sources_fingerprint() -> str:
    h = hashlib.sha256()
    for path in (PLANTS_PATH, ENRICHED_PATH, LEADS_PATH, ABSTRACTS_PATH):
        h.update(path.read_bytes() if path.exists() else b"")
    h.update(f"{EMBED_MODEL}|{CHUNK_SIZE}|{CHUNK_OVERLAP}".encode())
    return h.hexdigest()


def load_model():
    """Charge le modèle d'embeddings : copie locale (models/) si présente, sinon Hugging Face."""
    from sentence_transformers import SentenceTransformer  # import tardif : lent à charger
    source = str(LOCAL_MODEL_DIR) if (LOCAL_MODEL_DIR / "model.safetensors").exists() else EMBED_MODEL
    return SentenceTransformer(source, device="cpu")


def indication_vocabulary() -> list[dict]:
    """Termes d'indications uniques : {'modern': 'jaundice', 'old': 'yellow jaundice'}."""
    seen, vocab = set(), []
    for p in json.loads(ENRICHED_PATH.read_text(encoding="utf-8")):
        for raw in p.get("indications") or []:
            m = re.match(r"^\s*(.*?)\s*\((.*)\)\s*$", raw)
            modern, old = (m.group(1), m.group(2)) if m else (raw.strip(), None)
            key = (modern.lower(), (old or "").lower())
            if modern and key not in seen:
                seen.add(key)
                vocab.append({"modern": modern, "old": old})
    return vocab


class HybridIndex:
    def __init__(self, docs: list[dict], embeddings: np.ndarray, vocab: list[dict],
                 vocab_emb: np.ndarray, model=None):
        from rank_bm25 import BM25Okapi
        self.docs = docs
        self.embeddings = embeddings
        self.vocab = vocab
        self.vocab_emb = vocab_emb
        self.bm25 = BM25Okapi([d["tokens"] for d in docs])
        self._model = model
        self.plant_lookup = PlantLookup()
        self._last_query = ""
        self.last_expansion: list[str] = []
        self.last_modern: list[str] = []
        self.last_plants: list[dict] = []
        self.last_plant_words: set[str] = set()

    @property
    def model(self):
        if self._model is None:
            self._model = load_model()
        return self._model

    # ---------- construction / chargement
    @classmethod
    def load_or_build(cls, force: bool = False) -> "HybridIndex":
        manifest_path = INDEX_DIR / "manifest.json"
        abstracts = fetch_abstracts()  # sans appel réseau si tout est en cache
        fingerprint = sources_fingerprint()
        if not force and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("fingerprint") == fingerprint:
                docs = json.loads((INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
                emb = np.load(INDEX_DIR / "embeddings.npy")
                vocab = json.loads((INDEX_DIR / "vocab.json").read_text(encoding="utf-8"))
                vocab_emb = np.load(INDEX_DIR / "vocab_embeddings.npy")
                return cls(docs, emb, vocab, vocab_emb)
            print("Sources modifiées depuis le dernier index : reconstruction.")

        docs = build_documents(abstracts)
        for d in docs:
            d["tokens"] = tokenize(f"{d['plant']} {d.get('scientific_name') or ''} {d['text']}")
        print(f"Index : {len(docs)} passages à vectoriser avec {EMBED_MODEL} (une seule fois)…")
        model = load_model()
        emb = model.encode([embed_input(d) for d in docs], batch_size=64, show_progress_bar=True,
                           normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)

        vocab = indication_vocabulary()
        vocab_emb = model.encode([v["modern"] for v in vocab], batch_size=64,
                                 normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)

        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        np.save(INDEX_DIR / "embeddings.npy", emb)
        np.save(INDEX_DIR / "vocab_embeddings.npy", vocab_emb)
        (INDEX_DIR / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
        (INDEX_DIR / "chunks.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
        counts = {s: sum(d["source"] == s for d in docs) for s in SOURCE_LABEL}
        manifest_path.write_text(json.dumps({
            "fingerprint": fingerprint, "model": EMBED_MODEL, "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP, "n_chunks": len(docs), "by_source": counts,
            "built": date.today().isoformat(),
        }, indent=2), encoding="utf-8")
        print(f"Index enregistré dans {INDEX_DIR} : {counts}, {len(vocab)} termes d'indications")
        return cls(docs, emb, vocab, vocab_emb, model)

    # ---------- recherche
    def expand(self, query: str, q_emb: np.ndarray,
               translated: list[str] | None = None) -> tuple[list[str], list[str]]:
        """Termes d'indications proches de la requête : (termes modernes, termes anciens).

        On compare le vocabulaire à la question entière ET à chacun de ses mots et
        bigrammes significatifs : dans « Quelles plantes contre la jaunisse ? », c'est
        le mot « jaunisse » seul qui ressemble à « jaundice ».
        """
        words = tokenize(query)
        translated = translate_query(query) if translated is None else translated
        pieces = words + [f"{a} {b}" for a, b in zip(words, words[1:])] + translated
        sims = self.vocab_emb @ q_emb
        if pieces:
            p_emb = self.model.encode(pieces, normalize_embeddings=True, convert_to_numpy=True)
            sims = np.maximum(sims, (self.vocab_emb @ p_emb.T).max(axis=1))
        modern, old = list(dict.fromkeys(translated)), []
        best = float(sims.max()) if len(sims) else 0.0
        for i in np.argsort(-sims)[: EXPANSION_MAX_TERMS]:
            if sims[i] < EXPANSION_MIN_SIM or sims[i] < best - EXPANSION_MARGIN:
                break
            v = self.vocab[int(i)]
            if v["modern"] not in modern:
                modern.append(v["modern"])
            if v["old"] and v["old"] not in old:
                old.append(v["old"])
        return modern, old

    def search(self, query: str, k: int = 20) -> list[dict]:
        """Recherche hybride (BM25 + sémantique, requête enrichie) ; tri par score RRF."""
        self._last_query = query
        ranks: dict[int, dict] = {}
        q = self.model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]

        # Expansion : relie une question en français ou en termes modernes au
        # vocabulaire anglais du corpus et aux termes anciens de Culpeper.
        # Plantes nommées dans la question : leur nom chez Culpeper rejoint la requête
        # (« garlic » -> « Garlick », orthographe du livre).
        plants, plant_words = self.plant_lookup.find(query)
        self.last_plants, self.last_plant_words = plants, plant_words
        plant_names = [p["name"] for p in plants]
        # L'expansion ne porte que sur la partie "indication" de la question
        indication_part = " ".join(w for w in tokenize(query) if w not in plant_words) or query
        # (traduction faite sur la question complète : « maux de tête » garde son « de »)
        modern, old = self.expand(indication_part, q, translated=translate_query(query))
        self.last_modern = modern
        self.last_expansion = modern + old + [n for n in plant_names if n.lower() not in query.lower()]
        # RRF pondérée : quand la question correspond à une indication connue, la
        # correspondance exacte des termes (BM25 enrichi) est plus fiable que le petit
        # modèle multilingue, faible sur l'anglais du XVIIe siècle.
        weights = {"bm25": 1.0, "semantic": 0.5} if modern else {"bm25": 1.0, "semantic": 1.0}
        bm = self.bm25.get_scores(tokenize(" ".join([query, *modern, *old, *plant_names])))
        for r, i in enumerate([i for i in np.argsort(-bm)[:CANDIDATES] if bm[i] > 0], 1):
            ranks.setdefault(int(i), {})["bm25"] = r

        if modern or plant_names:  # la requête sémantique reçoit aussi ces termes anglais
            q = self.model.encode([f"{query} {' '.join(modern + plant_names)}"],
                                  normalize_embeddings=True, convert_to_numpy=True)[0]
        sims = self.embeddings @ q
        for r, i in enumerate(np.argsort(-sims)[:CANDIDATES], 1):
            ranks.setdefault(int(i), {})["semantic"] = r

        results = []
        for i, rk in ranks.items():
            rrf = sum(weights[m] / (RRF_K + r) for m, r in rk.items())
            results.append({**self.docs[i], "rrf": rrf, "ranks": rk, "similarity": float(sims[i])})
        results.sort(key=lambda d: d["rrf"], reverse=True)

        # Un seul passage (le meilleur) par plante chez Culpeper / par fiche / par article
        seen, unique = set(), []
        for d in results:
            key = (d["source"], d.get("pmid") or d["plant"])
            if key not in seen:
                seen.add(key)
                unique.append(d)
        return unique[:k]


# =========================================================================== #
# 4. Réponse avec Gemini (+ mode de secours)
# =========================================================================== #
def plan_targeted(index: HybridIndex, results: list[dict]) -> tuple[list[dict], list[str]]:
    """Plantes et termes d'indication pour la recherche PubMed ciblée.

    Plantes : celles nommées dans la question ; à défaut, les premières plantes
    (avec nom scientifique) des passages Culpeper / fiches trouvés.
    """
    terms = indication_terms(index._last_query, index.last_plant_words, index.last_modern)
    plants = list(index.last_plants)
    if not plants:
        for d in results:
            plant = index.plant_lookup.by_id.get(d.get("plant_id"))
            if (d["source"] in ("fiche", "culpeper") and plant and plant["scientific_name"]
                    and plant not in plants):
                plants.append(plant)
            if len(plants) >= TARGETED_MAX_PLANTS:
                break
    return plants[:TARGETED_MAX_PLANTS], terms


def select_context(results: list[dict], targeted: list[dict] | None = None) -> list[dict]:
    """Meilleurs passages de chaque source (ordre RRF) ; articles ciblés en tête de PubMed."""
    targeted = targeted or []
    quota = dict(CONTEXT_QUOTA)
    quota["pubmed"] = max(2, quota["pubmed"] - len(targeted))  # garde un peu de contexte général
    picked, counts = [], {s: 0 for s in quota}
    seen_pmids = {d["pmid"] for d in targeted}
    for r in results:
        if r["source"] == "pubmed" and r.get("pmid") in seen_pmids:
            continue
        if counts[r["source"]] < quota[r["source"]]:
            picked.append(r)
            counts[r["source"]] += 1
    ancient = [d for d in picked if d["source"] != "pubmed"]
    modern = [d for d in picked if d["source"] == "pubmed"]
    return ancient + targeted + modern


def source_line(d: dict) -> str:
    if d["source"] == "culpeper":
        return f"Culpeper, The Complete Herbal (1653) — chapitre « {d['plant']} »"
    if d["source"] == "pubmed":
        tag = "[recherche ciblée] " if d.get("targeted") else ""
        return f"{tag}{d['title']} ({d['year']}) — PMID {d['pmid']} — {d['url']}"
    return f"Fiche Panacea — {d['plant']} ({d.get('scientific_name')})"


def targeted_summary(targeted: list[dict], terms: list[str], context: list[dict] | None = None) -> str:
    """Bilan lisible de la recherche ciblée (pour le prompt et l'affichage)."""
    if not terms:
        return "Recherche PubMed ciblée : aucune indication identifiée dans la question."
    if not targeted:
        return "Recherche PubMed ciblée : aucune plante identifiée dans la question."
    lines = []
    for t in targeted:
        label = f"{t['plant']} ({t['scientific_name'] or '?'}) + {' / '.join(terms)}"
        if t["error"]:
            lines.append(f"- {label} : non interrogé — {t['error']}")
        elif not t["articles"]:
            lines.append(f"- {label} : AUCUN article trouvé.")
        else:
            nums = ""
            if context is not None:
                ids = {a["pmid"] for a in t["articles"]}
                nums = " " + "".join(f"[{i}]" for i, d in enumerate(context, 1)
                                     if d["source"] == "pubmed" and d.get("targeted") and d["pmid"] in ids)
            lines.append(f"- {label} : {len(t['articles'])} article(s){nums}")
    return "Recherche PubMed ciblée (nom scientifique AND indication) :\n" + "\n".join(lines)


RAG_PROMPT = """Tu es Panacea, un assistant qui croise un herbier ancien et la recherche moderne.

RÈGLES STRICTES
- Réponds en français.
- Utilise UNIQUEMENT les sources numérotées ci-dessous. N'ajoute aucune connaissance extérieure.
- Chaque affirmation doit être suivie du numéro de sa source entre crochets, ex. [2] ou [1][4].
- Structure obligatoire, avec ces deux titres :
  ## Savoir ancien (Culpeper, 1653)
  (sources de type Culpeper ou Fiche Panacea)
  ## Science moderne (PubMed)
  (sources de type PubMed uniquement ; commence par les articles marqués
  [recherche ciblée], qui portent précisément sur la plante ET l'indication)
- Si une partie ne trouve rien de pertinent dans les sources, écris-le clairement
  (ex. « Aucun article PubMed fourni ne traite de cette question. »). N'invente jamais.
- Si le bilan de recherche ciblée ci-dessous indique "AUCUN article trouvé" pour une
  plante, écris-le explicitement dans la partie Science moderne.
- Distingue bien ce que Culpeper affirmait de ce qui est démontré aujourd'hui.
- Termine par une section "## Sources" listant les numéros utilisés, puis par la phrase
  exacte : "Ceci n'est pas un avis médical."

QUESTION : {question}

{targeted}

SOURCES :
{sources}
"""


def format_sources(context: list[dict]) -> str:
    blocks = []
    for n, d in enumerate(context, 1):
        blocks.append(f"[{n}] ({SOURCE_LABEL[d['source']]}) {source_line(d)}\n{d['text']}")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Appel à Gemini en usage interactif : budget de temps strict, aucune attente
# --------------------------------------------------------------------------- #
# Modèles pour la réponse en direct : les « flash-lite » d'abord (mesurés à ~2 s sur une
# question Panacea, contre des 504 au-delà de 40 s pour gemini-3.8-flash saturé), puis les
# « flash » en secours. Réflexion réduite (« low ») pour limiter la latence.
# (L'extraction des fiches, src/extract.py, garde ses propres modèles « flash ».)
RAG_MODELS = [
    ("gemini-3.5-flash-lite", "low"),
    ("gemini-3.1-flash-lite", None),
    ("gemini-3.8-flash", "low"),
    ("gemini-3.6-flash", "low"),
]
GEMINI_CALL_TIMEOUT = 15.0    # secondes au plus par appel (par modèle)
GEMINI_TOTAL_TIMEOUT = 30.0   # secondes au plus en tout, modèles de secours compris
_QUOTA_BLOCKED: dict[str, float] = {}  # modèle -> instant jusqu'auquel son quota est épuisé
_GEMINI_CLIENT = None


class GeminiUnavailable(RuntimeError):
    """Aucun modèle n'a répondu dans le budget : `attempts` détaille chaque tentative."""

    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def log(msg: str) -> None:
    """Journal du terminal (visible dans la console qui a lancé Streamlit ou la CLI)."""
    print(f"[Panacea {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _gemini_client():
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        from dotenv import load_dotenv
        from google import genai
        from google.genai import types
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GeminiUnavailable("clé GEMINI_API_KEY absente du fichier .env", [])
        # Délai réseau aligné sur le budget par appel ; aucune nouvelle tentative interne au SDK
        _GEMINI_CLIENT = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=int(GEMINI_CALL_TIMEOUT * 1000)))
    return _GEMINI_CLIENT


def _classify(e: Exception) -> tuple[str, float]:
    """(cause lisible, durée de blocage du modèle en secondes) pour une erreur Gemini."""
    from google.genai import errors
    from src.extract import retry_delay
    if isinstance(e, errors.APIError):
        if e.code == 429:
            wait = retry_delay(e, 0)
            kind = "quota journalier épuisé" if wait > 120 else "quota par minute atteint"
            return f"{kind} (429, réessayer dans {wait / 60:.0f} min)", wait
        if e.code in (500, 502, 503, 504):
            return f"serveur surchargé ({e.code})", 0.0
        if e.code == 404:
            return "modèle indisponible (404)", 3600.0
        return f"erreur API {e.code} : {str(e)[:120]}", 0.0
    if isinstance(e, TimeoutError):
        return "délai dépassé", 0.0
    name = type(e).__name__
    if "timeout" in name.lower() or "timed out" in str(e).lower():
        return "délai réseau dépassé", 0.0
    return f"{name} : {str(e)[:120]}", 0.0


def answer_with_gemini(question: str, context: list[dict], targeted_note: str = "",
                       on_progress=None) -> tuple[str, str]:
    """Rédige la réponse avec Gemini ; renvoie (réponse, modèle).

    - 15 s au plus par appel, 30 s au plus en tout (modèles de secours compris) ;
    - quota épuisé ou erreur 503 : pas d'attente, on passe au modèle suivant ;
    - un modèle au quota épuisé est mis de côté pour la durée indiquée par l'API ;
    - chaque tentative est écrite dans le terminal ; `on_progress(modèle)` est appelé
      avant chaque essai (affichage de l'étape en cours dans l'interface).
    Lève GeminiUnavailable si rien n'a abouti : l'appelant passe en mode de secours.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    from google.genai import types

    start = time.monotonic()
    prompt = RAG_PROMPT.format(question=question, targeted=targeted_note, sources=format_sources(context))
    forced = os.getenv("GEMINI_MODEL")
    plan = [(forced, None)] if forced else list(RAG_MODELS)
    thinking = dict(plan)
    models = [m for m, _ in plan]
    attempts: list[dict] = []
    client = _gemini_client()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        for model in models:
            blocked_until = _QUOTA_BLOCKED.get(model, 0.0)
            if blocked_until > time.time():
                attempts.append({"model": model, "outcome": "ignoré",
                                 "detail": f"quota épuisé (encore {(blocked_until - time.time()) / 60:.0f} min)",
                                 "seconds": 0.0})
                log(f"Gemini {model} : ignoré, quota épuisé plus tôt dans la session")
                continue
            remaining = GEMINI_TOTAL_TIMEOUT - (time.monotonic() - start)
            if remaining < 2:
                attempts.append({"model": model, "outcome": "non essayé",
                                 "detail": "budget total de 30 s écoulé", "seconds": 0.0})
                log(f"Gemini {model} : non essayé, budget total de {GEMINI_TOTAL_TIMEOUT:.0f} s écoulé")
                break
            budget = min(GEMINI_CALL_TIMEOUT, remaining)
            if on_progress:
                on_progress(model)
            t0 = time.monotonic()
            config = (types.GenerateContentConfig(thinking_config=types.ThinkingConfig(
                thinking_level=thinking[model])) if thinking.get(model) else None)
            future = pool.submit(client.models.generate_content, model=model, contents=prompt, config=config)
            try:
                resp = future.result(timeout=budget)
                text = (resp.text or "").strip()
                if not text:
                    raise ValueError("réponse vide")
                took = time.monotonic() - t0
                attempts.append({"model": model, "outcome": "succès", "detail": "", "seconds": took})
                log(f"Gemini {model} : réponse en {took:.1f} s (total {time.monotonic() - start:.1f} s)")
                return text, model
            except FutureTimeout:
                future.cancel()  # le fil réseau s'arrêtera seul à son propre délai ; on n'attend pas
                cause, block = f"délai dépassé ({budget:.0f} s)", 0.0
            except Exception as e:  # quota, 503, réseau, réponse vide…
                cause, block = _classify(e)
            took = time.monotonic() - t0
            if block:
                _QUOTA_BLOCKED[model] = time.time() + block
            attempts.append({"model": model, "outcome": "échec", "detail": cause, "seconds": took})
            log(f"Gemini {model} : ÉCHEC après {took:.1f} s : {cause}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    total = time.monotonic() - start
    summary = " ; ".join(f"{a['model']} : {a['detail'] or a['outcome']}" for a in attempts) or "aucun modèle"
    log(f"Gemini indisponible après {total:.1f} s : mode de secours ({summary})")
    raise GeminiUnavailable(f"Gemini indisponible après {total:.0f} s ({summary})", attempts)


def diverse_top(results: list[dict], n: int, per_source: int = 2) -> list[dict]:
    """Les n meilleurs passages, au plus `per_source` par source tant que c'est possible."""
    picked = []
    for d in results:
        if len(picked) < n and sum(p["source"] == d["source"] for p in picked) < per_source:
            picked.append(d)
    for d in results:  # complète si une source manque de passages
        if len(picked) < n and d not in picked:
            picked.append(d)
    return sorted(picked, key=lambda d: d["rrf"], reverse=True)


def print_passages(results: list[dict], n: int) -> None:
    for i, d in enumerate(diverse_top(results, n), 1):
        how = " + ".join(f"{m} #{r}" for m, r in d["ranks"].items())
        print(f"\n[{i}] {SOURCE_LABEL[d['source']]} — {source_line(d)}")
        print(f"    pertinence RRF {d['rrf']:.4f} ({how})")
        print(textwrap.indent(textwrap.fill(d["text"], 100), "    "))


def print_targeted(targeted: list[dict], terms: list[str]) -> None:
    print("\n— " + targeted_summary(targeted, terms).replace(" :\n", " —\n", 1))
    for t in targeted:
        if t["query"]:
            print(f"\n  {t['plant']} — requête : {t['query']}")
        if t["error"]:
            print(f"    ✗ {t['error']}")
        elif not t["articles"]:
            print("    Aucun article trouvé pour cette combinaison plante + indication.")
        for a in t["articles"]:
            print(f"    • {a['title']} ({a['year']}) — PMID {a['pmid']} — {a['url']}")
            print(textwrap.indent(textwrap.fill(a["abstract"][:400] + ("…" if len(a["abstract"]) > 400 else ""),
                                                96), "      "))


def main() -> None:
    parser = argparse.ArgumentParser(description="Panacea — questions-réponses Culpeper × PubMed")
    parser.add_argument("question", nargs="?", help="question en français ou en anglais")
    parser.add_argument("--no-llm", action="store_true", help="recherche seule, sans appeler Gemini")
    parser.add_argument("--build", action="store_true", help="(re)construire l'index et quitter")
    parser.add_argument("--top", type=int, default=FALLBACK_TOP, help="passages affichés sans Gemini")
    args = parser.parse_args()

    index = HybridIndex.load_or_build(force=args.build)
    if args.build or not args.question:
        if not args.question:
            print('Index prêt. Usage : python src/rag.py "votre question"')
        return

    r = ask(index, args.question, use_llm=not args.no_llm, top=args.top)
    print(f"\nQuestion : {args.question}")
    if r["expansion"]:
        print(f"Requête enrichie avec : {', '.join(r['expansion'])}")

    if args.no_llm:
        print(f"\n— Recherche seule : {args.top} passages les plus pertinents —")
        print_passages(r["results"], args.top)
        print_targeted(r["targeted"], r["terms"])
        return

    if r["answer"] is None:  # mode de secours : jamais de plantage
        print(f"\n⚠ La réponse rédigée est momentanément indisponible ({r['error']}).")
        print(f"Voici les {args.top} passages les plus pertinents avec leurs sources :")
        print_passages(r["results"], args.top)
        print_targeted(r["targeted"], r["terms"])
        print("\nCeci n'est pas un avis médical.")
        return

    print("\n" + r["answer"] + f"\n\n_(modèle : {r['model']})_")
    print("\n— Sources citables —")
    for n, d in enumerate(r["context"], 1):
        print(f"[{n}] {source_line(d)}")


def ask(index: HybridIndex, question: str, use_llm: bool = True, top: int = FALLBACK_TOP) -> dict:
    """Pipeline complet d'une question (utilisé par la CLI et par l'application Streamlit).

    Renvoie un dictionnaire :
      results   passages classés (RRF)           top      les `top` passages affichés en secours
      expansion termes ajoutés à la requête      targeted bilan PubMed ciblé par plante
      terms     termes d'indication              context  sources numérotées envoyées à Gemini
      answer    réponse rédigée (None si secours) model    modèle Gemini utilisé
      error     cause de l'indisponibilité de Gemini (mode de secours)
    """
    out = search_phase(index, question, top)
    pubmed_phase(index, out)
    if use_llm:
        answer_phase(out)
    return out


# Les trois phases d'une question, séparées pour que l'interface affiche les sources
# dès qu'elles sont prêtes, puis la réponse rédigée quand elle arrive.
def search_phase(index: HybridIndex, question: str, top: int = FALLBACK_TOP) -> dict:
    """1. Recherche hybride dans l'index (Culpeper, résumés PubMed, fiches)."""
    t0 = time.monotonic()
    results = index.search(question, k=60)
    out = {"question": question, "results": results, "top": diverse_top(results, top),
           "expansion": list(index.last_expansion), "context": [], "answer": None,
           "model": None, "error": None, "attempts": [], "timings": {}, "terms": [], "targeted": []}
    out["timings"]["recherche"] = time.monotonic() - t0
    return out


def pubmed_phase(index: HybridIndex, out: dict) -> dict:
    """2. Recherche PubMed ciblée (plante AND indication) et contexte numéroté pour Gemini."""
    t0 = time.monotonic()
    plants, terms = plan_targeted(index, out["results"])
    targeted = targeted_pubmed(plants, terms) if (plants and terms) else []
    out.update(terms=terms, targeted=targeted,
               context=select_context(out["results"], targeted_docs(targeted)))
    out["timings"]["pubmed"] = time.monotonic() - t0
    return out


def answer_phase(out: dict, on_progress=None) -> dict:
    """3. Rédaction par Gemini (budget de 30 s) ; en cas d'échec, mode de secours."""
    t0 = time.monotonic()
    try:
        out["answer"], out["model"] = answer_with_gemini(
            out["question"], out["context"],
            targeted_summary(out["targeted"], out["terms"], out["context"]), on_progress=on_progress)
    except GeminiUnavailable as e:
        out["error"], out["attempts"] = str(e), e.attempts
    except Exception as e:  # dernier filet : jamais de plantage
        out["error"] = f"{type(e).__name__} : {e}"
        log(f"Gemini : erreur inattendue : {out['error']}")
    out["timings"]["redaction"] = time.monotonic() - t0
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
