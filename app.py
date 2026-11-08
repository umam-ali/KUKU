"""Promo Finder web app.   streamlit run app.py

Defaults: the bundled show (input/) and built-in keys (Streamlit secrets or .env).
Visitors can paste their own key, plug in any OpenAI-compatible API, or upload another show as a .zip.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import streamlit as st

from run import ep_range, run_pipeline
from src.llm import LLM, LLMError, load_config, load_dotenv

ROOT = Path(__file__).resolve().parent
BUNDLED_INPUT = ROOT / "input"   # only present locally (confidential screenplays are not in the repo)
DEMO = ROOT / "demo"             # precomputed results for the example show: summaries only, safe to publish
MAX_UPLOAD_MB = 50

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")


# ---------------- helpers ----------------
def builtin_key(name: str) -> str | None:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # no secrets file when running locally
        pass
    load_dotenv()
    import os
    return os.environ.get(name)


def bundled_title() -> str | None:
    files = sorted(BUNDLED_INPUT.rglob("*.txt")) if BUNDLED_INPUT.exists() else []
    return files[0].parent.name if files else None


def save_upload(files) -> Path:
    """Write uploaded .txt files / .zip archives into a fresh folder. Only .txt files are kept."""
    dest = Path(tempfile.mkdtemp(prefix="promo_in_"))
    n = 0
    for f in files:
        data = f.getvalue()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError(f"{f.name} is larger than {MAX_UPLOAD_MB} MB")
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = Path(info.filename).name  # flatten: no paths, no traversal
                    if info.is_dir() or not name.lower().endswith(".txt") or name.startswith("."):
                        continue
                    if info.file_size > 5 * 1024 * 1024:
                        continue
                    (dest / name).write_bytes(z.read(info))
                    n += 1
        elif f.name.lower().endswith(".txt"):
            (dest / Path(f.name).name).write_bytes(data)
            n += 1
    if not n:
        raise ValueError("No screenplay .txt files found in the upload")
    return dest


def load_results(out: Path) -> dict | None:
    if not (out / "promos.json").exists():
        return None
    res = {"acquisition": json.loads((out / "promos.json").read_text(encoding="utf-8"))}
    eng = out / "promos_engagement.json"
    res["engagement"] = json.loads(eng.read_text(encoding="utf-8")) if eng.exists() else []
    rows_path = out / "all_candidates_scored.json"
    rows = json.loads(rows_path.read_text(encoding="utf-8")) if rows_path.exists() else []
    res["scripts"] = {r["cid"]: r.get("script", "") for r in rows}  # empty for the demo: no screenplay text
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    res["n_candidates"] = len(rows) or meta.get("candidates", 0)
    res["title"] = meta.get("title")
    res["md"] = (out / "promos.md").read_text(encoding="utf-8") if (out / "promos.md").exists() else ""
    return res


def show_list(promos: list[dict], scripts: dict, key: str) -> None:
    if not promos:
        st.info("No clip passed the quality gates for this list.")
        return
    st.dataframe(
        [{"#": p["rank"], "Episode": p["episode"], "Start": p["segments"][0]["start"], "End": p["segments"][0]["end"],
          "Sec": round(p["duration_sec"]), "Hook (first 3 s)": p["hook"], "Cliffhanger": p["cliffhanger"],
          "Score": p["total"]} for p in promos],
        hide_index=True, width="stretch",
        column_config={"Hook (first 3 s)": st.column_config.TextColumn(width="large"),
                       "Cliffhanger": st.column_config.TextColumn(width="large")})
    for p in promos:
        seg = p["segments"][0]
        s = p["scores"]
        with st.expander(f"#{p['rank']}  ·  Episode {p['episode']}  ·  {seg['start']} – {seg['end']}  "
                         f"({p['duration_sec']:.0f} s)  ·  {p['hook']}"):
            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown(f"**Hook:** {p['hook']}  \n**Cliffhanger:** {p['cliffhanger']}  \n**Why it works:** {p['why']}")
                if p.get("post_first"):
                    st.markdown(f"**Why post it here:** {p['post_first']}")
                if p.get("missing_context"):
                    st.caption(f"A stranger might miss: {p['missing_context']}")
                for fl in p.get("flags") or []:
                    st.caption(f"⚠ {fl}")
            with c2:
                st.markdown(f"Hook **{s['hook']}** · Cliffhanger **{s['cliffhanger']}** · Standalone **{s['standalone']}**"
                            f" · Coherence **{s['coherence']}** · Emotion **{s['emotion']}** (out of 5)")
                st.caption(f"Cut from screenplay lines {seg['start_element']} to {seg['end_element']} · "
                           f"source: {'events' if p['pool'] == 'A' else 'episode ending' if p['pool'] == 'B' else 'episode opening'}")
            if scripts.get(p["cid"]):
                st.code(scripts[p["cid"]], language=None, wrap_lines=True)


def show_results(res: dict, title: str) -> None:
    st.subheader(title)
    a, e = res["acquisition"], res["engagement"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Clips considered", res["n_candidates"])
    m2.metric("Promos for new viewers", len(a))
    m3.metric("Episodes covered", len({p["episode"] for p in a}))
    t1, t2, t3 = st.tabs(["New viewers (acquisition)", "Lapsed viewers (engagement)", "Download"])
    with t1:
        st.caption("Ads for people who have never seen the show: must make sense alone; later twists are penalised.")
        show_list(a, res["scripts"], "acq")
    with t2:
        st.caption("For viewers who dropped off: backstory is fine, emotion counts more.")
        show_list(e, res["scripts"], "eng")
    with t3:
        st.download_button("promos.json (new viewers)", json.dumps(a, ensure_ascii=False, indent=1), "promos.json")
        st.download_button("promos_engagement.json", json.dumps(e, ensure_ascii=False, indent=1), "promos_engagement.json")
        if res["md"]:
            st.download_button("promos.md (editor report)", res["md"], "promos.md")


# ---------------- sidebar ----------------
st.sidebar.title("🎬 Promo Finder")
st.sidebar.caption("Finds 20–60 s promo clips in microdrama screenplays.")

st.sidebar.header("1 · API key")
has_builtin = bool(builtin_key("GEMINI_API_KEY"))
key_mode = st.sidebar.radio("Which key?", ["Built-in key", "Paste my own key"],
                            index=0 if has_builtin else 1, label_visibility="collapsed")
pasted = ""
if key_mode == "Paste my own key":
    pasted = st.sidebar.text_input("Gemini API key", type="password",
                                   help="Free at aistudio.google.com. Used only for this run, never stored.")
elif not has_builtin:
    st.sidebar.warning("No built-in key is configured. Paste your own.")

with st.sidebar.expander("Use another model / provider (optional)"):
    custom = st.checkbox("Use any OpenAI-compatible API instead of Gemini")
    c_url = st.text_input("Base URL", "https://api.openai.com/v1", disabled=not custom)
    c_key = st.text_input("API key", type="password", disabled=not custom)
    c_cheap = st.text_input("Model for reading and scoring (many calls)", "", disabled=not custom)
    c_strong = st.text_input("Model for the final re-score (few calls)", "", disabled=not custom)
    c_rpm = st.number_input("Requests per minute allowed", 5, 2000, 60, disabled=not custom)

st.sidebar.header("2 · Screenplays")
title = bundled_title()
src_opts = ([f"Bundled: {title}"] if title else []) + ["Upload a show (.zip or .txt)"]
src_mode = st.sidebar.radio("Source", src_opts, label_visibility="collapsed")
if not title:
    st.sidebar.caption("The example show’s results are shown on the right. Its screenplays are confidential, "
                       "so to run the tool, upload a show in the Kuku screenplay format.")
uploads = None
if src_mode.startswith("Upload"):
    uploads = st.sidebar.file_uploader("Screenplay files", type=["zip", "txt"], accept_multiple_files=True,
                                       help="Kuku screenplay format: one .txt per episode, or a .zip of them.")

st.sidebar.header("3 · Options")
ep_spec = st.sidebar.text_input("Episodes", "all", help="'all', or a range like 1-10 (faster).")
fast = st.sidebar.checkbox("Fast mode (skip the stronger re-score)", value=src_mode.startswith("Upload"),
                           key=f"fast_{src_mode[:6]}",
                           help="The stronger model is slow on free keys. Fast mode uses the first-pass scores only.")
run_clicked = st.sidebar.button("Find promos", type="primary", width="stretch")

# ---------------- main ----------------
st.title("Promo Finder for AI Microdramas")
st.markdown("Reads a show’s screenplays and returns a **ranked shortlist of 20–60 s promo clips**, each with its hook, "
            "its cliffhanger and why it works, for an editor to approve. "
            "AI reads the story; code makes the cuts, so every clip maps to real timestamps.")

with st.expander("How long does it take?"):
    st.markdown("- **Bundled show, default settings:** about a minute (results are cached).\n"
                "- **Uploaded show, fast mode, free keys:** about 2–4 min for 10 episodes, about 15 min for 50 "
                "(the free tier allows 15 calls a minute).\n"
                "- **Without fast mode:** add up to 2 min for the stronger re-score (capped; if the free model is "
                "overloaded, clips keep their first-pass scores).\n"
                "- **Paid keys / higher limits:** a few minutes.")

if run_clicked:
    try:
        cfg = load_config()
        keys = {}
        if custom:
            if not (c_key and c_cheap and c_strong):
                raise ValueError("For a custom provider, fill in the API key and both model names.")
            cfg["providers"]["custom"] = {"base_url": c_url.rstrip("/"), "api_key_env": "CUSTOM_API_KEY"}
            for role, model in [("episode", c_cheap), ("score", c_cheap), ("series", c_strong), ("rescore", c_strong)]:
                cfg["roles"][role] = {"provider": "custom", "model": model}
            for model in {c_cheap, c_strong}:
                cfg["limits"][model] = {"rpm": int(c_rpm), "tpm": 10_000_000, "rpd": 100_000}
            keys["CUSTOM_API_KEY"] = c_key
        elif key_mode == "Paste my own key":
            if not pasted:
                raise ValueError("Paste a Gemini API key in the sidebar, or switch to the built-in key.")
            keys["GEMINI_API_KEY"] = pasted.strip()
        else:
            keys["GEMINI_API_KEY"] = builtin_key("GEMINI_API_KEY") or ""

        if src_mode.startswith("Upload"):
            if not uploads:
                raise ValueError("Upload a .zip (or .txt files) of screenplays first.")
            folder = save_upload(uploads)
        else:
            folder = BUNDLED_INPUT
        episodes = None if ep_spec.strip().lower() in ("", "all") else ep_range(ep_spec.strip())
        out = Path(tempfile.mkdtemp(prefix="promo_out_"))

        bar = st.progress(0.0, text="Starting…")
        logbox = st.status("Running the pipeline…", expanded=True)
        lines: list[str] = []
        log_area = logbox.empty()

        def log(msg: str) -> None:
            lines.append(msg)
            log_area.code("\n".join(lines[-14:]), language=None)

        def stage(name: str, frac: float) -> None:
            bar.progress(min(max(frac, 0.0), 1.0), text=name + "…")

        summary = run_pipeline(folder, out, cfg, LLM(cfg, keys=keys), episodes=episodes, fast=fast,
                               log=log, stage=stage)
        logbox.update(label=f"Done in {summary.get('seconds', 0)} s", state="complete", expanded=False)
        st.session_state["result_dir"] = str(out)
        st.session_state["result_title"] = (f"Results: {title}" if folder == BUNDLED_INPUT else "Results: uploaded show")
    except (ValueError, LLMError) as e:
        st.error(str(e))
    except Exception as e:  # keep the page alive and show what broke
        st.exception(e)

res_dir = st.session_state.get("result_dir")
if res_dir and (res := load_results(Path(res_dir))):
    show_results(res, st.session_state.get("result_title", "Results"))
elif res := load_results(DEMO):
    show_results(res, f"Example results: {res.get('title') or 'example show'} (50 episodes, precomputed)")
else:
    st.info("Choose your screenplays and press **Find promos** in the sidebar.")
