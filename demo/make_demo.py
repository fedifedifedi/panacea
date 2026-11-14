"""Vidéo de démonstration de Panacea (1920×1080, mode sombre, voix FR, sous-titres EN).

Étapes (toutes rejouables) :
  1. voix       narration française scène par scène (voix neuronale fr-FR-HenriNeural, edge-tts)
  2. tournage   l'application réelle pilotée dans Edge (protocole DevTools), calée sur la voix
  3. montage    images + voix + sous-titres anglais incrustés -> MP4 H.264 prêt pour LinkedIn

Prérequis : l'application tourne (streamlit run app/main.py --server.port 8599), ffmpeg installé.
Usage :     python demo/make_demo.py            (tout)
            python demo/make_demo.py --skip-tts (réutilise les voix déjà générées)
Sorties :   demo/output/panacea_demo.mp4, panacea_demo_en.srt, panacea_demo_fr.srt
"""

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "output"
FRAMES = OUT / "frames"
APP_URL = "http://localhost:8599/"
VOICE = "fr-FR-HenriNeural"
WIDTH, HEIGHT = 1920, 1080
GAP = 0.45            # silence entre deux scènes (s)
OUTRO_HOLD = 2.0      # le carton final reste affiché après la voix (s)
QUESTION = "L'ail contre les infections ?"

# --------------------------------------------------------------------------- #
# Script : narration française, sous-titres anglais, actions à l'écran
# --------------------------------------------------------------------------- #
SCENES = [
    {"id": "accueil",
     "fr": "Et si les remèdes de demain dormaient dans un livre de 1653 ? Voici Panacea : une plateforme "
           "qui relit l'herbier de Nicholas Culpeper avec l'intelligence artificielle, pour retrouver "
           "les remèdes oubliés.",
     "en": "What if tomorrow's remedies were hiding in a book from 1653? Meet Panacea: a platform that "
           "rereads Nicholas Culpeper's herbal with artificial intelligence to rediscover forgotten remedies."},
    {"id": "decouvertes",
     "fr": "Trois cent vingt-huit plantes, plus de quatre mille molécules et quinze mille articles "
           "scientifiques croisés. Culpeper recommandait par exemple la grande camomille contre les maux "
           "de tête : la science l'étudie aujourd'hui contre la migraine.",
     "en": "328 plants, over 4,000 molecules and 15,000 scientific articles, cross-referenced. Culpeper "
           "recommended feverfew for headaches; today, science studies it to prevent migraine."},
    {"id": "extraction",
     "fr": "Pour chaque plante, l'intelligence artificielle extrait les indications, les préparations, et "
           "une citation exacte du texte, vérifiée mot pour mot.",
     "en": "For each plant, the AI extracts its uses, its preparations, and an exact quote from the text, "
           "verified word for word."},
    {"id": "graphe",
     "fr": "Toutes ces connaissances forment un graphe : plantes, maladies et molécules, reliées entre elles.",
     "en": "All this knowledge forms a graph linking plants, diseases and molecules."},
    {"id": "pistes",
     "fr": "Panacea calcule ensuite un score de piste innovante : un usage ancien riche, mais peu d'études "
           "aujourd'hui. En bas à droite, la zone des remèdes oubliés, où se détache All-Heal.",
     "en": "Panacea then computes an innovation score: rich traditional use, but few studies today. "
           "Bottom right: the forgotten-remedies zone, where All-Heal stands out."},
    {"id": "questions",
     "fr": "On peut enfin l'interroger en langage naturel. Panacea cherche dans le texte de 1653 et dans "
           "PubMed, puis rédige une réponse sourcée, qui sépare savoir ancien et science moderne.",
     "en": "Finally, you can ask it questions in plain language. Panacea searches the 1653 text and "
           "PubMed, then writes a sourced answer that separates ancient knowledge from modern science."},
    {"id": "reponse",
     "fr": "La réponse cite Culpeper d'un côté, les articles PubMed de l'autre, chacun avec un lien vers "
           "sa source.",
     "en": "The answer quotes Culpeper on one side and PubMed articles on the other, each linked to its "
           "source."},
    {"id": "fin",
     "fr": "Panacea : du savoir des herbiers anciens aux pistes de recherche d'aujourd'hui. "
           "Ceci n'est pas un avis médical.",
     "en": "Panacea: from ancient herbals to today's research leads. This is not medical advice."},
]


def log(msg: str) -> None:
    print(f"[démo {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- #
# 1. Voix
# --------------------------------------------------------------------------- #
async def synthesize() -> None:
    import edge_tts
    for i, sc in enumerate(SCENES, 1):
        path = OUT / f"voix_{i:02d}_{sc['id']}.mp3"
        for attempt in range(4):  # réseau parfois instable
            try:
                await edge_tts.Communicate(sc["fr"], VOICE, rate="-4%").save(str(path))
                break
            except Exception as e:
                log(f"voix {sc['id']} : nouvel essai ({e})")
                await asyncio.sleep(2)
        log(f"voix {i}/{len(SCENES)} : {path.name} ({ffprobe_duration(path):.1f} s)")


def timeline() -> list[dict]:
    """Début et durée de chaque scène, d'après les voix générées."""
    t, plan = 0.6, []
    for i, sc in enumerate(SCENES, 1):
        audio = OUT / f"voix_{i:02d}_{sc['id']}.mp3"
        d = ffprobe_duration(audio)
        plan.append({**sc, "audio": audio, "start": t, "dur": d})
        t += d + GAP
    return plan


# --------------------------------------------------------------------------- #
# 2. Tournage (protocole DevTools d'Edge, enregistrement « screencast »)
# --------------------------------------------------------------------------- #
class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.frames = ws, 0, {}, []
        self.recording = False

    async def reader(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg.get("result", {}))
            elif msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                if self.recording:
                    idx = len(self.frames)
                    (FRAMES / f"f{idx:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                    self.frames.append(p["metadata"]["timestamp"])
                asyncio.ensure_future(self.call("Page.screencastFrameAck", sessionId=p["sessionId"]))

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_event_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        return await fut

    async def js(self, expr: str):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")


HIDE_CHROME = """
(() => { const s = document.createElement('style');
  s.textContent = 'header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"]'
    + '{display:none!important} ::-webkit-scrollbar{display:none} *{scrollbar-width:none}';
  document.head.appendChild(s); return true; })()"""


def js_scroll(selector: str, index: int = 0, block: str = "start") -> str:
    return (f"(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{index}];"
            f" if (!e) return false; e.scrollIntoView({{behavior: 'smooth', block: '{block}'}}); return true; }})()")


def js_click_tab(name: str) -> str:
    return (f"(() => {{ const t = [...document.querySelectorAll('[role=tab]')]"
            f".find(x => x.innerText.includes({json.dumps(name)})); if (!t) return false; t.click(); return true; }})()")


def js_click_button(text: str) -> str:
    return (f"(() => {{ const b = [...document.querySelectorAll('button')]"
            f".find(x => x.innerText.trim().startsWith({json.dumps(text)})); if (!b) return false; b.click(); return true; }})()")


async def wait_for(cdp: CDP, expr: str, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await cdp.js(expr):
            return True
        await asyncio.sleep(0.25)
    return False


async def at(t0: float, offset: float) -> None:
    """Attend l'instant `offset` (s) de la vidéo."""
    delay = t0 + offset - time.time()
    if delay > 0:
        await asyncio.sleep(delay)


async def record(plan: list[dict], cdp_port: int) -> float:
    import websockets
    page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json"))
                if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=500 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        reader = asyncio.ensure_future(cdp.reader())
        await cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT,
                       deviceScaleFactor=1, mobile=False)
        await cdp.call("Page.enable")

        # Préchauffage (autre session) : charge l'index et le modèle de recherche une fois
        log("préchauffage de l'application…")
        await cdp.call("Page.navigate", url=APP_URL)
        await wait_for(cdp, "!!document.querySelector('.st-key-hero')", 60)
        await asyncio.sleep(2)
        await cdp.js(js_click_tab("Questions"))
        await wait_for(cdp, "!!document.querySelector('input[aria-label^=\"Votre question\"]')", 20)
        await cdp.js("document.querySelector('input[aria-label^=\"Votre question\"]').focus()")
        await cdp.call("Input.insertText", text="Quelles plantes contre la toux ?")
        await asyncio.sleep(.3)
        await cdp.js(js_click_button("Rechercher"))
        await wait_for(cdp, "!!document.querySelector('.st-key-ans_old, .pa-notice')", 60)

        # Page neuve pour le tournage
        await cdp.call("Page.navigate", url=APP_URL)
        await wait_for(cdp, "!!document.querySelector('.st-key-hero') && !!document.querySelector('.pa-disc')", 60)
        await cdp.js(HIDE_CHROME)
        await asyncio.sleep(3)  # polices et graphiques chargés

        await cdp.call("Page.startScreencast", format="jpeg", quality=92, maxWidth=WIDTH,
                       maxHeight=HEIGHT, everyNthFrame=1)
        await asyncio.sleep(.5)
        cdp.recording = True
        t0 = time.time()
        log("tournage…")
        S = {sc["id"]: sc for sc in plan}

        # 1. Accueil : le bandeau, puis un léger défilement
        await at(t0, S["accueil"]["start"] + S["accueil"]["dur"] * .72)
        await cdp.js("(document.querySelector('[data-testid=\"stMain\"]')||document.scrollingElement)"
                     ".scrollBy({top: 160, behavior: 'smooth'})")

        # 2. Chiffres clés, puis la découverte « grande camomille »
        sc = S["decouvertes"]
        await at(t0, sc["start"] - .2)
        await cdp.js(js_scroll(".pa-kpi-tile", 0, "center"))
        await at(t0, sc["start"] + sc["dur"] * .48)
        await cdp.js(js_scroll(".pa-disc", 1, "center"))

        # 3. Onglet Extraction
        sc = S["extraction"]
        await at(t0, sc["start"] - .3)
        await cdp.js(js_click_tab("Extraction"))
        await asyncio.sleep(.6)
        await cdp.js(js_scroll("[role=tablist]", 0, "start"))
        await at(t0, sc["start"] + sc["dur"] * .55)
        await cdp.js(js_scroll("[data-testid='stDataFrame']", 0, "center"))

        # 4. Onglet Graphe
        sc = S["graphe"]
        await at(t0, sc["start"] - .5)
        await cdp.js(js_click_tab("Graphe"))
        await asyncio.sleep(.8)
        await cdp.js(js_scroll("[role=tablist]", 0, "start"))

        # 5. Onglet Pistes
        sc = S["pistes"]
        await at(t0, sc["start"] - .3)
        await cdp.js(js_click_tab("Pistes"))
        await asyncio.sleep(.8)
        await cdp.js(js_scroll("[role=tablist]", 0, "start"))

        # 6. Onglet Questions : saisie, envoi, réponse rédigée en direct
        sc = S["questions"]
        await at(t0, sc["start"] - .4)
        await cdp.js(js_click_tab("Questions"))
        await wait_for(cdp, "!!document.querySelector('input[aria-label^=\"Votre question\"]')", 10)
        await cdp.js(js_scroll("[role=tablist]", 0, "start"))
        await asyncio.sleep(.6)
        await cdp.js("document.querySelector('input[aria-label^=\"Votre question\"]').focus()")
        for ch in QUESTION:  # frappe naturelle
            await cdp.call("Input.insertText", text=ch)
            await asyncio.sleep(.055)
        await asyncio.sleep(.35)
        await cdp.js(js_click_button("Rechercher"))
        answered = await wait_for(cdp, "!!document.querySelector('.st-key-ans_old, .pa-notice')", 32)
        log(f"réponse affichée : {'oui' if answered else 'non'} ({time.time() - t0:.1f} s)")

        # 6 bis. La réponse rédigée, bien visible pendant sa propre narration
        sc = S["reponse"]
        await at(t0, sc["start"] - .6)
        await cdp.js(js_scroll(".st-key-ans_old, .pa-notice", 0, "start"))
        await at(t0, sc["start"] + sc["dur"] * .6)
        await cdp.js(js_scroll(".st-key-ans_new, .pa-notice", 0, "center"))

        # 7. Carton final
        sc = S["fin"]
        await at(t0, sc["start"] - .3)
        await cdp.call("Page.navigate", url=(OUT / "outro.html").resolve().as_uri())
        end = sc["start"] + sc["dur"] + OUTRO_HOLD
        await at(t0, end)
        cdp.recording = False
        await cdp.call("Page.stopScreencast")
        reader.cancel()
        log(f"tournage terminé : {len(cdp.frames)} images")
        (OUT / "frames.json").write_text(json.dumps({"t0": t0, "end": end, "ts": cdp.frames}))
        return end


def write_outro() -> None:
    """Carton final (même charte que l'application, mode sombre)."""
    mark = ROOT / "app" / "assets" / "panacea_mark.png"
    logo = base64.b64encode(mark.read_bytes()).decode() if mark.exists() else ""
    (OUT / "outro.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Cormorant+Garamond:wght@700&display=swap" rel="stylesheet">
<style>
html,body{{margin:0;height:100%;background:#0A1120;color:#E7EDF5;font-family:Inter,Segoe UI,sans-serif}}
.w{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;
   background:radial-gradient(ellipse at 50% 35%,#0B3D2E 0%,#0A1120 62%)}}
img{{width:132px;height:132px;border-radius:50%;background:#fff}}
h1{{font-family:'Cormorant Garamond',Georgia,serif;font-size:118px;letter-spacing:.14em;margin:0}}
p{{margin:0;font-size:34px;color:#C9D4E3}} .gh{{color:#4FD1A1;font-size:30px;margin-top:18px}}
.f{{position:absolute;bottom:56px;font-size:22px;color:#9AA8BC}}
</style></head><body><div class="w"><img src="data:image/png;base64,{logo}">
<h1>PANACEA</h1><p>Du savoir des herbiers anciens aux pistes de recherche d'aujourd'hui</p>
<div class="gh">github.com/fedifedifedi/panacea</div>
<div class="f">Prototype · Fedi Limem, élève ingénieur IA &amp; Data Science · Ceci n'est pas un avis médical</div>
</div></body></html>""", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 3. Sous-titres et montage
# --------------------------------------------------------------------------- #
def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def cues(text: str, start: float, dur: float, max_len: int = 84) -> list[tuple[float, float, str]]:
    """Découpe le texte en sous-titres courts (≤ 2 lignes), répartis au prorata de leur longueur."""
    parts, cur = [], ""
    for chunk in re.split(r"(?<=[.?!:;,])\s+", text):
        if cur and len(cur) + len(chunk) + 1 > max_len:
            parts.append(cur)
            cur = chunk
        else:
            cur = f"{cur} {chunk}".strip()
    parts.append(cur)
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = dur * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def write_srt(plan: list[dict], lang: str, path: Path) -> None:
    n, lines = 1, []
    for sc in plan:
        for a, b, txt in cues(sc[lang], sc["start"], sc["dur"]):
            lines += [str(n), f"{srt_time(a)} --> {srt_time(b - .05)}", txt, ""]
            n += 1
    path.write_text("\n".join(lines), encoding="utf-8")


def build(plan: list[dict]) -> Path:
    meta = json.loads((OUT / "frames.json").read_text())
    t0, end, ts = meta["t0"], meta["end"], meta["ts"]
    # Images à durée variable (le screencast n'émet qu'aux changements d'écran)
    lines = ["ffconcat version 1.0"]
    for i, t in enumerate(ts):
        nxt = ts[i + 1] if i + 1 < len(ts) else t0 + end
        start = max(t, t0) if i else t0
        dur = max(nxt - start, 1 / 60)
        lines += [f"file 'frames/f{i:06d}.jpg'", f"duration {dur:.4f}"]
    lines.append(f"file 'frames/f{len(ts) - 1:06d}.jpg'")
    (OUT / "frames.ffconcat").write_text("\n".join(lines))

    write_srt(plan, "en", OUT / "panacea_demo_en.srt")
    write_srt(plan, "fr", OUT / "panacea_demo_fr.srt")

    # Voix : chaque scène placée à son instant, puis normalisation (-16 LUFS, réseaux sociaux)
    inputs, filters = [], []
    for i, sc in enumerate(plan):
        inputs += ["-i", str(sc["audio"])]
        ms = int(sc["start"] * 1000)
        filters.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(plan)))
    filters.append(f"{mix}amix=inputs={len(plan)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

    style = ("FontName=Segoe UI,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BackColour=&H99000000,BorderStyle=4,Outline=0,Shadow=0,MarginV=34,Bold=1")
    out = OUT / "panacea_demo.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "frames.ffconcat",
           *inputs, "-filter_complex", ";".join(filters),
           "-vf", f"fps=30,scale={WIDTH}:{HEIGHT}:flags=lanczos,format=yuv420p,"
                  f"subtitles=panacea_demo_en.srt:force_style='{style}'",
           "-map", "0:v", "-map", "[aout]", "-t", f"{end:.2f}",
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out.name]
    # -vf et -filter_complex ne peuvent coexister : on fusionne la vidéo dans filter_complex
    vf = cmd[cmd.index("-vf") + 1]
    del cmd[cmd.index("-vf"):cmd.index("-vf") + 2]
    fc = cmd.index("-filter_complex") + 1
    cmd[fc] = f"[0:v]{vf}[vout];" + cmd[fc]
    cmd[cmd.index("0:v")] = "[vout]"
    subprocess.run(cmd, cwd=OUT, check=True)

    # Version « propre » (sans sous-titres ni son) : matière première des animations du portfolio
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "frames.ffconcat",
                    "-vf", f"fps=30,scale={WIDTH}:{HEIGHT}:flags=lanczos,format=yuv420p", "-t", f"{end:.2f}",
                    "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "panacea_demo_clean.mp4"],
                   cwd=OUT, check=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Vidéo de démonstration Panacea")
    parser.add_argument("--skip-tts", action="store_true", help="réutiliser les voix déjà générées")
    parser.add_argument("--cdp-port", type=int, default=9444)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if not args.skip_tts:
        asyncio.run(synthesize())
    plan = timeline()
    log("plan : " + ", ".join(f"{p['id']} {p['start']:.1f}+{p['dur']:.1f}s" for p in plan))
    write_outro()
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    asyncio.run(record(plan, args.cdp_port))
    out = build(plan)
    log(f"vidéo : {out} ({out.stat().st_size / 1e6:.1f} Mo, {ffprobe_duration(out):.1f} s)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
