"""Étape 2 : extraction structurée des connaissances avec Google Gemini.

Pour les N premières plantes de data/plants.json, demande à un modèle Gemini
"flash" d'extraire indications, parties utilisées, préparations, etc.

- Cache (data/cache/gemini_extract.json) : une plante déjà traitée n'est jamais
  renvoyée à l'API.
- Respect du niveau gratuit : pause entre les appels, nouvelles tentatives
  automatiques sur les erreurs de quota (429) et de surcharge (503), puis bascule
  sur un autre modèle flash si le modèle principal reste indisponible.
- Garde-fou anti-invention : on vérifie que source_quote figure mot pour mot
  dans le texte source.

Par défaut, traite la sélection SELECTED_PLANTS (20 plantes connues). Les fiches
de data/cache/claude_extract.json (rédigées par Claude quand le quota Gemini est
épuisé) sont fusionnées dans la sortie avec "extracted_by": "claude".

Usage : python src/extract.py [--limit N] [--pause 7]
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PLANTS_PATH = DATA_DIR / "plants.json"
OUTPUT_PATH = DATA_DIR / "extracted.json"
CACHE_PATH = DATA_DIR / "cache" / "gemini_extract.json"
# Fiches rédigées à la main par Claude (même format) quand le quota Gemini est épuisé.
CLAUDE_CACHE_PATH = DATA_DIR / "cache" / "claude_extract.json"

# Sélection de plantes médicinales connues et bien documentées (id dans plants.json).
SELECTED_PLANTS = {
    60: "Camomile", 166: "St. John’s Wort", 266: "Sage", 256: "Rosemary",
    173: "Lavender", 327: "Wormwood", 328: "Yarrow", 315: "Garden Valerian",
    201: "Mint", 121: "Fennel", 307: "Thyme", 135: "Garlick",
    5: "Agrimony", 132: "Fox-Glove", 242: "Poppy", 120: "Feverfew",
    100: "Dandelion", 86: "Comfrey", 180: "Liquorice", 23: "Balm",
}

# Modèle principal puis modèles de secours (tous "flash", disponibles en niveau gratuit).
# On peut imposer un modèle via la variable d'environnement GEMINI_MODEL.
# Le niveau gratuit limite chaque modèle à ~20 requêtes/jour : la chaîne de secours
# permet de traiter davantage de plantes par jour.
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
MAX_QUOTA_WAIT = 120      # au-delà, c'est le quota journalier : on change de modèle


class DailyQuotaExceeded(Exception):
    """Quota journalier du modèle épuisé (l'API demande d'attendre des heures)."""

DEFAULT_LIMIT = 40
DEFAULT_PAUSE = 7.0       # secondes entre deux appels (~8 requêtes/minute)
MAX_RETRIES = 6           # tentatives par modèle sur 429 (quota)
MAX_OVERLOAD_RETRIES = 2  # tentatives par modèle sur 5xx avant de passer au modèle suivant
REQUEST_TIMEOUT_MS = 90_000
MAX_JSON_RETRIES = 2      # nouvelles demandes si la réponse n'est pas un JSON valide


# --------------------------------------------------------------------------- #
# Schéma de sortie
# --------------------------------------------------------------------------- #
class PlantExtraction(BaseModel):
    plant_name: str = Field(description="Name of the plant as written in the text.")
    scientific_name: Optional[str] = Field(
        description="Modern Latin binomial if the plant can be identified from its name "
        "and description, otherwise null."
    )
    scientific_name_confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in scientific_name. Use 'low' when scientific_name is null."
    )
    indications: Optional[list[str]] = Field(
        description="Diseases or symptoms the text says the plant treats, in simple modern "
        "English, with the old term in parentheses if different, e.g. "
        "'jaundice (yellow jaundice)'. Null if none."
    )
    parts_used: Optional[list[str]] = Field(
        description="Plant parts the text says are used (leaves, root, seed...). Null if none."
    )
    preparations: Optional[list[str]] = Field(
        description="Preparations mentioned in the text (decoction, ointment, juice...). Null if none."
    )
    ancient_dosage: Optional[str] = Field(
        description="Dosage exactly as stated in the text (e.g. 'a dram of the powder'). Null if none."
    )
    source_quote: Optional[str] = Field(
        description="One short sentence copied VERBATIM from the text that supports the "
        "indications. Null if there are no indications."
    )


SYSTEM_PROMPT = """You extract structured data from Nicholas Culpeper's "The Complete Herbal" (1653).

Strict rules:
- Use ONLY the text provided. Never add uses, parts, preparations or dosages from your own knowledge.
- If a piece of information is not in the text, return null (not an empty string, not a guess).
- The single exception is scientific_name: you may identify the plant from its name and
  description using botanical knowledge. If you cannot identify it reasonably, return null
  with confidence "low". Use "high" only when the identification is unambiguous.
- Ignore astrological statements (planets, signs) when listing indications.
- indications: modern, simple English terms; put Culpeper's old term in parentheses when it
  differs, e.g. "menstrual disorders (the terms)", "kidney stones (the stone and gravel)".
- source_quote: copy one short sentence character-for-character from the text. It must
  explicitly name at least one of the listed indications (a disease or symptom). Never pick
  a sentence about witchcraft, magic, astrology or the author's opinions."""


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def load_cache(path: Path = CACHE_PATH) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"⚠ Cache illisible, ignoré : {path}")
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)  # écriture atomique : pas de cache corrompu si on interrompt


def cache_key(plant: dict) -> str:
    return f"{plant['id']}:{plant['name']}"


# --------------------------------------------------------------------------- #
# Appel à Gemini
# --------------------------------------------------------------------------- #
def retry_delay(err: errors.APIError, attempt: int) -> float:
    """Délai conseillé par l'API (RetryInfo) sinon backoff exponentiel."""
    details = err.details if isinstance(err.details, dict) else {}
    for d in details.get("error", {}).get("details", []) or []:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1)) + 1
    return min(10 * 2 ** attempt, 120)


def parse_json(text: str) -> PlantExtraction:
    """Parse la réponse en tolérant des balises ```json éventuelles."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        cleaned = m.group(0) if m else cleaned
    return PlantExtraction.model_validate(json.loads(cleaned))


def call_model(client: genai.Client, model: str, plant: dict) -> PlantExtraction:
    """Un appel avec gestion quota/surcharge et JSON invalide. Lève une exception si échec."""
    prompt = (
        f"Plant heading: {plant['heading']}\n"
        f"Other names: {', '.join(plant['aliases']) or 'none'}\n\n"
        f"TEXT:\n\"\"\"\n{plant['text']}\n\"\"\""
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=PlantExtraction,
    )
    json_failures = 0
    attempt = 0
    while True:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            return parse_json(resp.text)
        except errors.APIError as e:
            limit = MAX_RETRIES if e.code == 429 else MAX_OVERLOAD_RETRIES
            if e.code in (429, 500, 503, 504) and attempt < limit:
                wait = retry_delay(e, attempt) if e.code == 429 else 15 * (attempt + 1)
                if e.code == 429 and wait > MAX_QUOTA_WAIT:
                    raise DailyQuotaExceeded(f"{model} : quota journalier épuisé "
                                             f"(réessayer dans {wait / 3600:.1f} h)") from e
                label = "quota atteint" if e.code == 429 else f"serveur indisponible ({e.code})"
                print(f"    ↻ {label}, nouvelle tentative dans {wait:.0f}s "
                      f"({attempt + 1}/{limit})")
                time.sleep(wait)
                attempt += 1
                continue
            raise
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            json_failures += 1
            if json_failures > MAX_JSON_RETRIES:
                raise ValueError(f"JSON invalide après {json_failures} essais : {e}") from e
            print(f"    ↻ réponse JSON invalide ({type(e).__name__}), nouvel essai")
            time.sleep(2)


def extract_plant(client: genai.Client, models: list[str], plant: dict) -> tuple[PlantExtraction, str]:
    """Essaie chaque modèle de la liste ; renvoie (résultat, modèle utilisé)."""
    last_error: Exception | None = None
    for model in list(models):
        try:
            return call_model(client, model, plant), model
        except DailyQuotaExceeded as e:
            print(f"    ✗ {e} -> modèle écarté pour cette session")
            models.remove(model)  # inutile de le réessayer pour les plantes suivantes
            last_error = e
            continue
        except errors.APIError as e:
            last_error = e
            if e.code in (404, 429, 500, 503, 504):
                print(f"    ✗ {model} indisponible ({e.code}), essai du modèle suivant")
                continue
            raise
    raise RuntimeError(f"Tous les modèles ont échoué : {last_error}")


# --------------------------------------------------------------------------- #
# Garde-fou : la citation doit exister dans le texte
# --------------------------------------------------------------------------- #
def _normalize(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_is_verbatim(quote: str | None, text: str) -> bool | None:
    if not quote:
        return None
    return _normalize(quote).rstrip(".") in _normalize(text)


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
def print_summary(results: list[dict]) -> None:
    with_sci = [r for r in results if r.get("scientific_name")]
    unverified = [r["plant_name"] for r in results if r.get("source_quote_verified") is False]
    print("\n" + "=" * 70)
    print(f"Plantes traitées           : {len(results)}")
    for src in ("gemini", "claude"):
        print(f"  extraites par {src:<7}   : {sum(1 for r in results if r['extracted_by'] == src)}")
    print(f"Avec un nom scientifique   : {len(with_sci)}")
    by_conf = {c: sum(1 for r in with_sci if r["scientific_name_confidence"] == c)
               for c in ("high", "medium", "low")}
    print(f"  dont confiance           : {by_conf}")
    if unverified:
        print(f"Citations non retrouvées mot pour mot ({len(unverified)}) : {', '.join(unverified)}")
    print(f"Résultats -> {OUTPUT_PATH}")
    print("=" * 70)

    examples = sorted(with_sci, key=lambda r: len(r.get("indications") or []), reverse=True)[:2]
    for r in examples or results[:2]:
        print("\n" + json.dumps(r, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Extraction des connaissances avec Gemini")
    parser.add_argument("--limit", type=int, default=None,
                        help="traiter les N premières plantes (ordre du livre) au lieu de la sélection")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="pause entre appels (s)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY manquante : renseignez-la dans le fichier .env")

    forced = os.getenv("GEMINI_MODEL")
    models = [forced] if forced else list(MODELS)
    # Pas de retry interne au SDK (on gère nous-mêmes) + délai max par requête.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )

    all_plants = json.loads(PLANTS_PATH.read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in all_plants}
    if args.limit:
        plants = all_plants[: args.limit]
    else:
        plants = [by_id[i] for i in SELECTED_PLANTS]
        for i, expected in SELECTED_PLANTS.items():
            assert by_id[i]["name"] == expected, f"id {i} : {by_id[i]['name']} ≠ {expected}"

    cache = load_cache()
    claude_cache = load_cache(CLAUDE_CACHE_PATH)
    todo = [p for p in plants if cache_key(p) not in cache and cache_key(p) not in claude_cache]
    print(f"{len(plants)} plantes demandées, {len(plants) - len(todo)} déjà extraites, "
          f"{len(todo)} à traiter (modèle : {models[0]})\n")

    failures = []
    for n, plant in enumerate(todo, 1):
        if not models:
            print("\n⛔ Quota journalier épuisé sur tous les modèles. "
                  "Relancez le script demain : les plantes déjà traitées sont en cache.")
            failures.extend(p["name"] for p in todo[n - 1:])
            break
        print(f"[{n}/{len(todo)}] {plant['name']}")
        try:
            data, model_used = extract_plant(client, models, plant)
        except Exception as e:  # on continue avec les autres plantes ; pas de mise en cache
            print(f"    ✗ échec : {e}")
            failures.append(plant["name"])
            continue

        record = {
            "id": plant["id"],
            **data.model_dump(),
            "source_quote_verified": quote_is_verbatim(data.source_quote, plant["text"]),
            "extracted_by": "gemini",
            "model": model_used,
        }
        cache[cache_key(plant)] = record
        save_cache(cache)
        print(f"    ✓ {record['scientific_name'] or '—'} "
              f"({len(record['indications'] or [])} indications)")
        if n < len(todo):
            time.sleep(args.pause)

    # Résultats : plantes demandées + toutes les fiches déjà payées en cache.
    # Gemini prioritaire ; à défaut, la fiche rédigée par Claude (validée et vérifiée ici).
    keys = [cache_key(p) for p in plants]
    keys += [k for k in list(cache) + list(claude_cache) if k not in keys]
    results = []
    for key in keys:
        if key in cache:
            rec = {**cache[key]}
            rec.setdefault("extracted_by", "gemini")
        elif key in claude_cache:
            plant = by_id[int(key.split(":", 1)[0])]
            data = PlantExtraction.model_validate(
                {k: v for k, v in claude_cache[key].items() if k in PlantExtraction.model_fields})
            rec = {
                "id": plant["id"],
                **data.model_dump(),
                "source_quote_verified": quote_is_verbatim(data.source_quote, plant["text"]),
                "extracted_by": "claude",
                "model": claude_cache[key].get("model", "claude"),
            }
        else:
            continue
        results.append(rec)
    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if failures:
        print(f"\n⚠ {len(failures)} plante(s) en échec (relancez le script pour réessayer) : "
              f"{', '.join(failures)}")
    print_summary(results)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
