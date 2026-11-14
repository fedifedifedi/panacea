"""Nettoie le texte de Culpeper et le découpe en une entrée par plante.

Entrée  : data/culpeper_complete_herbal.txt (téléchargé si absent)
Sortie  : data/plants.json
"""

import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.download import DATA_DIR, RAW_PATH, download

OUTPUT_PATH = DATA_DIR / "plants.json"

# Bornes de la partie "herbier" du livre (la suite est le dispensaire / les recettes).
HERBAL_START = "AMARA DULCIS."
HERBAL_END = "DIRECTIONS FOR MAKING SYRUPS"

# Un titre de plante : ligne indentée de 2 à 8 espaces, entièrement en majuscules.
HEADING_RE = re.compile(r"^ {2,8}([A-Z][^a-z]{2,})$")

# Séparateurs entre le nom principal et les noms alternatifs dans un titre.
ALIAS_SPLIT_RE = re.compile(
    r",?\s+OR,?\s+"
    r"|,?\s+(?:VULGARLY\s+)?CALLED\s+(?:ALSO\s+|BY\s+SOME\s+|IN\s+SOME\s+COUNTIES,?\s+)?"
    r"|,\s*USUALLY\s+KNOWN\s+BY\s+THE\s+NAME\s+OF\s+"
)
SMALL_WORDS = {"or", "and", "of", "the", "by", "in", "with", "a"}


# --------------------------------------------------------------------------- #
# Nettoyage
# --------------------------------------------------------------------------- #
def strip_gutenberg(text: str) -> str:
    """Retire l'en-tête et la licence du Projet Gutenberg."""
    start = re.search(r"\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", text)
    end = re.search(r"\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK", text)
    return text[start.end() if start else 0 : end.start() if end else len(text)]


def clean_body(lines: list[str]) -> str:
    """Recolle les lignes coupées en paragraphes et retire le balisage Gutenberg."""
    paragraphs, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    cleaned = []
    for p in paragraphs:
        p = p.replace("_", "")                 # italiques _texte_
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            cleaned.append(p)
    return "\n\n".join(cleaned)


# --------------------------------------------------------------------------- #
# Noms
# --------------------------------------------------------------------------- #
def nice_case(s: str) -> str:
    """'ADDER’S TONGUE OR SERPENT’S' -> 'Adder’s Tongue or Serpent’s'."""
    def cap(part: str, first: bool) -> str:
        return part if (not first and part in SMALL_WORDS) else part[:1].upper() + part[1:]

    words = s.lower().split()
    return " ".join(
        "-".join(cap(part, i == 0 and j == 0) for j, part in enumerate(w.split("-")))
        for i, w in enumerate(words)
    )


def parse_heading(heading: str) -> tuple[str, list[str]]:
    """Renvoie (nom principal, noms alternatifs) à partir du titre en majuscules."""
    h = heading.strip().rstrip(".,").strip()
    parts = [p.strip(" ,.;") for p in ALIAS_SPLIT_RE.split(h) if p.strip(" ,.;")]
    name, aliases = parts[0], parts[1:]
    # "SPLEENWORT, CETERACH, OR HEART'S TONGUE" -> nom "SPLEENWORT", alias "CETERACH"
    if aliases and "," in name:
        first, *rest = [x.strip() for x in name.split(",") if x.strip()]
        name, aliases = first, rest + aliases
    # "WHORTS, AND WHORTLE-BERRIES" -> ["WHORTS", "WHORTLE-BERRIES"]
    aliases = [x.strip() for a in aliases for x in re.split(r",\s*(?:AND\s+)?|\s+AND\s+", a) if x.strip()]
    aliases = [re.sub(r"^MORE\s+PROPERLY\s+", "", a) for a in aliases]
    name = re.sub(r"^THE\s+", "", name)
    return nice_case(name), [nice_case(a) for a in aliases]


# --------------------------------------------------------------------------- #
# Découpage
# --------------------------------------------------------------------------- #
def split_plants(text: str) -> list[dict]:
    lines = strip_gutenberg(text).split("\n")

    start = next(i for i, l in enumerate(lines) if l.strip() == HERBAL_START)
    end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith(HERBAL_END))

    # Repère les titres ; un titre peut s'étaler sur deux lignes consécutives.
    headings: list[tuple[int, int, str]] = []  # (ligne début, ligne fin, texte)
    i = start
    while i < end:
        if HEADING_RE.match(lines[i]):
            j, parts = i, []
            while j < end and HEADING_RE.match(lines[j]):
                parts.append(lines[j].strip())
                j += 1
            headings.append((i, j, " ".join(parts)))
            i = j
        else:
            i += 1

    plants = []
    for k, (h_start, h_end, heading) in enumerate(headings):
        body_end = headings[k + 1][0] if k + 1 < len(headings) else end
        name, aliases = parse_heading(heading)
        plants.append({
            "id": k + 1,
            "name": name,
            "aliases": aliases,
            "heading": heading,
            "text": clean_body(lines[h_end:body_end]),
        })
    return plants


def main() -> None:
    raw_path = RAW_PATH if RAW_PATH.exists() else download()
    text = raw_path.read_text(encoding="utf-8")
    plants = split_plants(text)

    OUTPUT_PATH.write_text(json.dumps(plants, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(plants)} plantes trouvées -> {OUTPUT_PATH}\n")
    for p in (plants[0], plants[len(plants) // 2], plants[-1]):
        aka = f"  (aussi : {', '.join(p['aliases'])})" if p["aliases"] else ""
        excerpt = p["text"][:300].replace("\n\n", " ")
        print(f"#{p['id']} {p['name']}{aka}")
        print(f"   {len(p['text']):,} caractères — {excerpt}…\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
