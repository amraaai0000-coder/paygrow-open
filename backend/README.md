# SpeakCart - backend

Voice/text grocery intent -> Spoonacular products -> PayPal sandbox checkout.

```
speakcart/
├── render.yaml               Render blueprint (one-click deploy)
└── backend/
    ├── requirements.txt      runtime deps
    ├── requirements-dev.txt  + pytest
    ├── .env.example          copy to .env for local dev
    ├── app/
    │   ├── config.py         every key read from env vars
    │   ├── models.py         pydantic request/response schemas
    │   ├── parser.py         English / Hinglish / Devanagari intent parser
    │   ├── main.py           FastAPI routes
    │   └── services/
    │       ├── spoonacular.py  product search (+ offline mock catalog)
    │       └── paypal.py       Orders v2: create + capture (+ mock)
    ├── static/index.html     browser demo client (also served at "/")
    └── tests/test_api.py     pytest, runs with zero keys
```

## Why it is built this way

Two things in the original plan do not behave the way the plan assumes, so the
code is defensive about both:

1. **Spoonacular does not guarantee a price.** `/food/products/search` returns
   `title` and `image`; a `price` field appears only sometimes. Every product
   therefore carries `price_source: "api" | "simulated"`, and simulated prices
   are deterministic (hashed from the title) so the demo total never jumps
   around between runs. Say this out loud to the judges instead of hiding it.
2. **PayPal is a three-step flow, not one click.** Create order -> user approves
   in a browser -> capture. `POST /api/checkout/paypal/capture` must be called
   after approval or no money moves. Also, PayPal India cannot receive domestic
   INR, so the default currency is USD. Two smaller traps are handled in the
   code: the order is created with only a total (PayPal item quantities must be
   whole numbers, so a "2.5 kg" line would be rejected), and `RETURN_URL` /
   `CANCEL_URL` must be absolute `http(s)` URLs - a custom scheme such as
   `speakcart://` is rejected by PayPal.

## What Spoonacular actually returns (checked against its docs)

Worth knowing before you promise anything on stage:

- `GET /food/products/search` returns only `id`, `title` and `imageType` for each
  product. There is no price in the search response at all.
  (<https://spoonacular.com/food-api/docs#Search-Grocery-Products>)
- `GET /food/products/{id}` is the endpoint that carries `price`, together with
  `brand`, `aisle`, `badges`, `nutrition`, `servings` and `spoonacularScore`.
  Its `price` unit is undocumented: the docs' own example returns `324` for a
  10.5 oz bag, which reads as cents, and some products return `0`. So the code
  reports `price_raw`, derives `price`, and labels it `api_assumed_cents`,
  `api` or `simulated`. (<https://spoonacular.com/food-api/docs#Get-Product-Information>)
- **There is no stock or availability field anywhere in the API.** The
  `availability` value this backend returns is generated locally from a hash of
  the product id and is always tagged `availability_source: "simulated"`. Do not
  tell judges it is live inventory.

Set `ALLOW_MOCK=false` in the environment if you want the built-in fallback
catalog switched off entirely - then a missing key returns a clear error instead
of sample products.

Additionally: Spoonacular's catalog is English-first, so the parser translates
Hindi/Hinglish item words ("दूध", "doodh" -> `milk`) before searching. Without
that layer a Hindi voice demo returns empty results.

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in your keys, or leave blank for mock mode
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/> for the browser demo, <http://localhost:8000/docs>
for the interactive API docs, and <http://localhost:8000/health> to see whether
each integration is `live` or `mock`.

With no keys at all the whole flow still works end to end (mock catalog, mock
orders) - useful while you build the Flutter UI.

Run the tests:

```bash
cd backend && pytest -q
```

## Agentic flow (voice in, voice out)

```
mic audio ──► Sarvam Saaras (speech-to-text)  ──► text
text ──► Groq Llama 3.1 8B ──► shopping plan (items + quantities)
plan ──► Spoonacular ──► numbered product cards
cards ──► Groq ──► spoken narration (count, every card by number, then
                   "which item do you want to book? tell me the card number")
narration ──► chunked to TTS_CHUNK_CHARS ──► Sarvam Bulbul (text-to-speech)
          ──► base64 WAV chunks + the matching text for each chunk
front end ──► plays each chunk and types its text out as the audio plays
```

One request does the whole loop: `POST /api/agent/voice` (or `/api/agent/understand`
for typed input) returns the cards, the narration text and the narration audio
already split into chunks, so the browser can start speaking immediately and
reveal the words in step with the audio.

If `SARVAM_API_KEY` or `GROQ_API_KEY` is missing, the same endpoints still answer:
Groq falls back to the rule-based parser and a template narration, and Sarvam
falls back to text-only chunks. `narration_engine`, `llm_engine` and each chunk's
`error` field tell you which path was used, so nothing silently pretends to be live.

### Language is detected, not hardcoded

Nothing in the request has to name a language. The order of evidence is:

1. `language` in the request, if the caller explicitly set one;
2. the language Sarvam Saaras heard in the audio (`language_code` in the
   speech-to-text response);
3. `POST /api/sarvam text-lid` on the transcript, when a Sarvam key is present;
4. a local script check (Devanagari, Tamil, Telugu, Bengali, Kannada, Malayalam,
   Gujarati, Gurmukhi, Odia) plus a Hinglish word list for Latin-script input;
5. `SARVAM_TTS_LANGUAGE` only as a last resort.

The resolved language is returned as `language` in the response and is what the
narration is written and spoken in. Send `"language": "auto"` (the browser demo's
default) to use the whole chain.

### Which LLM does the thinking

`LLM_PROVIDER` decides the order: `sarvam` (default) tries Sarvam Chat
Completions first and Groq second; `groq` does the reverse; `auto` behaves like
`sarvam`. Within a provider several model ids are tried in turn, so a retired
model never breaks the agent:

- Sarvam: `sarvam-105b` (default, `SARVAM_LLM_MODEL`) then `sarvam-m`, then
  `sarvam-105b-conversations`. Note that Sarvam retired `sarvam-m` (24B) from
  the Chat Completions API - it is only tried first in case an older account
  still has access.
- Groq: `GROQ_MODEL` then `llama-3.3-70b-versatile`, `openai/gpt-oss-20b`,
  `qwen/qwen3-32b`, `meta-llama/llama-4-scout-17b-16e-instruct`, `gemma2-9b-it`.

The engine that answered is reported as `llm_engine` (`sarvam` / `groq` /
`rules`) and, when everything failed, `llm_note` says why.

### When Spoonacular runs out of points

The free plan is a **daily points quota** (50 points/day at the time of writing,
reset at midnight UTC = 05:30 IST). A product search costs about 1 point plus
0.01 per result, so a busy afternoon of testing can use it up. Three things keep
the demo alive:

1. **Search cache** - an identical query is served from memory for
   `SPOONACULAR_CACHE_TTL_SECONDS` (default 1 hour) and costs nothing.
2. **Open Food Facts fallback** - on HTTP 402 the same search is retried against
   `world.openfoodfacts.org` (free, no key, no quota). It has names and images
   but no prices, so prices there are simulated. India coverage is thin (~10k
   products), so results may be sparse.
3. **Sample catalogue** - if Open Food Facts also returns nothing and
   `ALLOW_MOCK=true`, the built-in catalogue answers, labelled `mock-quota`.

Every product carries `source` (`spoonacular` / `openfoodfacts` / `mock`) and
`price_source`, and `/health` reports `spoonacular_quota` (`used_today`,
`last_request_cost`, read from the `X-API-Quota-Used` and `X-API-Quota-Request`
response headers) plus `spoonacular_cache` entries. Say which source answered if
a judge asks - none of this is hidden.

### Streaming endpoints (for the Flutter voice window)

`POST /voice-turn-stream` and `POST /tts-stream` answer with Server-Sent Events,
one event per line group, each carrying both an SSE `event:` name and a matching
`type` field inside the JSON:

```
event: transcript
data: {"type":"transcript","text":"mujhe ek bread chahiye","language_code":"hi-IN"}
```

`/voice-turn-stream` body:

```json
{"audio_base64": "...", "sample_rate": 16000, "language": "auto",
 "cards": [], "conversation_history": [], "voice": "saurav",
 "user_id": "...", "chat_id": "..."}
```

Event order: `start` -> `transcript` -> `thinking` -> `stage` -> `narrate` ->
`chunk` (one per sentence, with `audio_base64` and `words`) -> `reply_text` ->
`done`. Any failure arrives as `error` with a `message` in plain words. `done`
carries `intent`, `language`, `cards` and, when a card was booked, the PayPal
`order` (including `approve_url`).

Audio is synthesised sentence by sentence, so the first chunk can play while the
rest is still being generated.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | `live` / `mock` status of each integration |
| POST | `/api/parse` | `{"text": "2 litre milk"}` -> structured items |
| POST | `/api/search` | text -> product cards from Spoonacular |
| POST | `/api/agent/understand` | text -> numbered cards + spoken narration + audio chunks |
| POST | `/api/agent/voice` | mic audio -> transcript -> the same agent response |
| POST | `/api/agent/tts` | speak any text (used for "say it again") |
| GET | `/api/product/{id}` | full product information: brand, aisle, badges, nutrition, price |
| POST | `/api/checkout/paypal/order` | cart -> `order_id` + `approve_url` |
| GET | `/api/checkout/paypal/order/{id}` | order state: CREATED / APPROVED / COMPLETED |
| POST | `/api/checkout/paypal/capture` | `order_id` -> captured payment |

Example:

```python
import requests

response = requests.post(
    "http://localhost:8000/api/search",
    json={"text": "2 litre milk aur ek bread", "number": 3},
)
print(response.json())
```

## Where to get the API keys

**Spoonacular**

1. Sign up at <https://spoonacular.com/food-api/console#Dashboard>.
2. The free plan gives 150 points/day. A product search costs about 1 point, so
   a demo run of a few items is well inside it. `402` from the API means the
   daily quota is finished.
3. Copy the key from the dashboard - that is `SPOONACULAR_API_KEY`.

**PayPal sandbox**

1. Go to <https://developer.paypal.com/dashboard/> and log in (a normal PayPal
   account works).
2. Apps & Credentials -> make sure the toggle is on **Sandbox** -> Create App.
3. Copy the **Client ID** and **Secret** (secret needs one click to reveal).
   These are `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
4. Under Sandbox -> Accounts you will find pre-made test accounts; log in with a
   *personal* sandbox account in another browser profile to approve payments
   without spending real money.
5. Keep `PAYPAL_BASE=https://api-m.sandbox.paypal.com`. Never switch this to the
   live host during the hackathon.

## Deploy on Render

Render sets `$PORT` for you and reads config from environment variables, which
is exactly what `app/config.py` does - nothing is hardcoded.

**Option A - Blueprint (fastest)**

1. Push this repo to GitHub.
2. Render dashboard -> **New +** -> **Blueprint** -> pick the repo.
   `render.yaml` at the repo root is detected automatically (`rootDir: backend`).
3. Render asks for the three secrets (`sync: false` in the blueprint). Paste
   `SPOONACULAR_API_KEY`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`.
4. Apply. Build: `pip install -r requirements.txt`. Start:
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Hit `https://<your-service>.onrender.com/health` - both integrations should
   read `live`.

**Option B - manual web service**

1. **New +** -> **Web Service** -> connect the repo.
2. Root Directory: `backend` · Runtime: Python 3 · Plan: Free.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Environment -> add the same five variables:
   `SPOONACULAR_API_KEY`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`,
   `PAYPAL_BASE` (`https://api-m.sandbox.paypal.com`), `CURRENCY` (`USD`).
6. Health Check Path: `/health`. Deploy.

Notes for Render specifically:

- The free plan spins down after ~15 minutes idle, so open the URL a minute
  before you record the demo - the first request wakes it up.
- Never commit `.env`; `.gitignore` already excludes it.
- If you ever rotate a key, change it in Environment -> Save; Render redeploys
  and the app picks it up on restart.

## Connecting the Flutter app

Point the app at the deployed base URL:

```bash
flutter run --dart-define=API_BASE=https://<your-service>.onrender.com
```

On an Android emulator use `http://10.0.2.2:8000` for a local backend -
`localhost` there points at the emulator itself, not your machine.
