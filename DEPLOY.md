# Deploying the Promo Finder website (free, Streamlit Community Cloud)

The public site shows **example results** for *I Married a Broken Soldier. He Was a Duke* from `demo/`. These are
summaries only, with no screenplay text. Visitors can upload a show as a `.zip` and run it with the built-in key or
their own.

## What is and isn't in the repo
- **In the repo:** code, prompts, docs, `eval/` notes and `demo/`.
- **Never in the repo** (blocked by `.gitignore`): `input/` (Kuku's confidential screenplays), and `cache/` and
  `output/` (both contain screenplay lines).
- Your key lives in **Streamlit Secrets**, never in the code.
- Share the full raw outputs (`output/`) by private Drive link instead.

## Deploy
1. Push this folder to GitHub.
2. Go to **share.streamlit.io → Create app**. Choose your repo, branch `main` and main file **`app.py`**.
3. Open **Advanced settings**. Set Python **3.12**, and under **Secrets** paste (double quotes are required):
   ```
   GEMINI_API_KEY = "your-gemini-key"
   ```
4. Click **Deploy**. To update a deployed app, push to `main` and it redeploys itself.

## Timing on free keys
- **Uploaded show, fast mode:** about 2–4 minutes for 10 episodes, about 15 minutes for 50.
- **Without fast mode:** at most 2 extra minutes for the stronger re-score.

Run locally with `streamlit run app.py`. With `input/` present locally, the bundled show can also be re-run.
