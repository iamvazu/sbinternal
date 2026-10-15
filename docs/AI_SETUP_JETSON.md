# Running the AI layer for free on the Jetson AGX Orin 64GB

**Recommendation: use the Jetson.** It gives unlimited analysis, no per-token bills, no rate limits, and
your client profiles never leave the office. Its 64 GB of unified memory comfortably runs a 26–32B-parameter
model at 4-bit quantization. That's the size class that writes reliable, structured bid analyses.
Small 3B–8B models get the JSON shape right, but they miss requirements buried in long IFBs.

## 1. Prepare the Jetson (one time, ~30 min)

```bash
# Confirm JetPack 6.x (L4T R36.x). Reflash with NVIDIA SDK Manager if older.
cat /etc/nv_tegra_release

# Max performance mode (MAXN) and pinned clocks
sudo nvpmodel -m 0
sudo jetson_clocks

# Optional: free ~1-2 GB by booting to console instead of the desktop
sudo systemctl set-default multi-user.target

# Use an NVMe SSD for models; the eMMC is too small and slow
df -h

# Monitoring
sudo pip3 install -U jetson-stats && sudo reboot   # then run: jtop
```

## 2. Install Ollama

**Option A, native install (simplest):** the official installer detects JetPack and pulls the Jetson CUDA build.

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Option B, container:** use NVIDIA's community `jetson-containers` project if you prefer Docker.

```bash
git clone https://github.com/dusty-nv/jetson-containers && bash jetson-containers/install.sh
jetson-containers run $(autotag ollama)
```

Make it reachable from the office LAN and keep the model loaded (Option A):

```bash
sudo systemctl edit ollama
# add:
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
# Environment="OLLAMA_KEEP_ALIVE=24h"
# Environment="OLLAMA_NUM_PARALLEL=1"
# Environment="OLLAMA_MODELS=/mnt/nvme/ollama"     # if models live on the SSD
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

## 3. Pull a model

| Role | Model (Ollama tag) | RAM (Q4) | Notes |
|---|---|---|---|
| **Default** | `gemma4:26b` | ~18 GB | Already on your Windows box. Strong at long documents and JSON. |
| Alternative | `qwen3:30b-a3b` | ~19 GB | Mixture-of-experts (~3B active), so very fast on Orin; good structured output. |
| Max quality, overnight | `qwen3:32b` or `llama3.3:70b` | 20 / 43 GB | Dense, slower; 70B is only for overnight batch runs. |
| Fast triage | `llama3.2:3b` | 2 GB | Quick summaries only; too weak for compliance extraction. |

```bash
ollama pull gemma4:26b
ollama run gemma4:26b "Say ready"     # first load takes ~1 min
```

Model availability changes quickly. Run `ollama list` / check ollama.com/library and try two candidates
on the same 10 bids before you commit (see "Evaluate" below).

**Context length:** the analyzer defaults to `BI_LLM_NUM_CTX=16384` (≈36k characters of documents).
On 64 GB you can raise it to `32768` with `BI_CONTEXT_CHARS=90000` for the giant RFPs. That's slower,
but more of each document gets read.

## 4. Run Bid Intelligence on the Jetson

```bash
git clone <this repo>   # or copy the folder, including attachments/
cd "<repo>/bidintel"
pip3 install pymupdf python-docx openpyxl beautifulsoup4
python3 ingest.py && python3 enrich.py
python3 analyze.py --limit 5            # smoke test, check timings
nohup python3 analyze.py --all > analyze.log 2>&1 &   # whole backlog overnight
python3 analyze.py --brief
BI_HOST=0.0.0.0 python3 server.py       # dashboard at http://<jetson-ip>:8765
```

Or keep the dashboard on Windows and point it at the Jetson:

```powershell
$env:BI_LLM_URL = "http://<jetson-ip>:11434"; python server.py
```

**Daily automation** (crontab -e on the Jetson): scrape, ingest, enrich, analyze new bids, refresh the brief.

```cron
30 5 * * 1-5  cd /home/<you>/repo && python3 Caleprocure_targeted_scraper.py --headless && cd bidintel && python3 ingest.py && python3 enrich.py && python3 analyze.py --all && python3 analyze.py --brief >> daily.log 2>&1
```

`analyze.py` caches by input hash, so each morning it only spends GPU time on bids that are
new or have changed (new addenda, new version).

## 5. Throughput, honestly

Measure on your unit with `python3 analyze.py --limit 5`; it prints the seconds per bid. Expect a
26–30B Q4 model on Orin MAXN to take roughly 1–4 minutes per bid with a 16k context. Most of that
is prompt processing of the documents, and the MoE models are at the fast end. So the full backlog of
~360 bids is a one-night job, and a normal day's 10–30 new bids take well under an hour.

## 6. Evaluate before trusting it

Pick 10 bids your team already knows well. Run them with two models (`BI_LLM_MODEL=... python3 analyze.py --ids ... --force`)
and check three things against the PDFs: (1) are key dates correct? (2) are licenses and DVBE % correct?
(3) is anything invented? The rule-detected signals panel shows the exact text behind each flag, so
use it to spot-check. Keep the model that is right most often, not the one that writes nicest.

## Other free routes (if you don't want to run hardware)

All of these work with `BI_LLM_BACKEND=openai`, `BI_LLM_URL=...`, `BI_LLM_API_KEY=...`, `BI_LLM_MODEL=...`.

| Route | Cost | Catch |
|---|---|---|
| **Jetson + Ollama** (recommended) | $0 (electricity, ~30-60W) | One bid at a time; you maintain the box |
| Windows PC + Ollama | $0 | This PC has no NVIDIA GPU, so it runs on CPU. Fine for testing, too slow for daily volume |
| Google Gemini API free tier | $0 | Daily request caps. Free-tier prompts may be used to improve Google's models, so keep client data out |
| Groq / OpenRouter `:free` / Cloudflare Workers AI | $0 | Rate- and token-limited; models and limits change without notice |

Cal eProcure data is public, so cloud free tiers are acceptable for the bid analysis itself. Client
profiles and match results are STRONGERbuilt's business data, so keep those on the Jetson.
