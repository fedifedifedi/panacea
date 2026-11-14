"""Télécharge "The Complete Herbal" de Nicholas Culpeper depuis le Projet Gutenberg."""

from pathlib import Path

import requests

GUTENBERG_ID = 49513  # Culpeper's Complete Herbal
URLS = [
    f"https://www.gutenberg.org/cache/epub/{GUTENBERG_ID}/pg{GUTENBERG_ID}.txt",
    f"https://www.gutenberg.org/ebooks/{GUTENBERG_ID}.txt.utf-8",
]
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_PATH = DATA_DIR / "culpeper_complete_herbal.txt"


def download(force: bool = False) -> Path:
    """Télécharge le texte brut (sauf s'il existe déjà) et renvoie son chemin."""
    if RAW_PATH.exists() and not force:
        print(f"Texte déjà présent : {RAW_PATH}")
        return RAW_PATH

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Panacea/0.1 (educational project)"}
    last_error = None
    for url in URLS:
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            text = resp.text.replace("\r\n", "\n").replace("\r", "\n")
            RAW_PATH.write_text(text, encoding="utf-8", newline="\n")
            print(f"Téléchargé depuis {url} -> {RAW_PATH} ({len(resp.text):,} caractères)")
            return RAW_PATH
        except requests.RequestException as exc:
            last_error = exc
            print(f"Échec avec {url} : {exc}")
    raise RuntimeError(f"Impossible de télécharger le texte : {last_error}")


if __name__ == "__main__":
    download(force=True)
