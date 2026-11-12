# PayGrow

Speak your grocery list. PayGrow finds the products, reads them back to you, and
books the card you pick. PayPal takes the payment.

Built for the PayPal AI Hackathon 2026.

## The two halves

`backend/` is a FastAPI service. Sarvam's Saaras turns speech into text and
Bulbul turns text back into speech. An LLM works out what the shopper wants and
writes the spoken summary. Spoonacular supplies the catalogue. PayPal Orders v2
creates, approves and captures the payment.

`frontend/` is the Android app in Flutter. Email login with a one time code, a
chat screen, a voice window that runs the whole turn on its own, and an order
history drawer.

## One turn, end to end

1. The app records 16 kHz mono WAV and posts it to `/api/agent/voice`, or sends
   typed text to `/api/agent/understand`.
2. Saaras transcribes it and reports the language. Nothing is hardcoded: if the
   shopper speaks Tamil, the answer comes back in Tamil.
3. The intent decides what happens next. `search` looks for products, `select`
   books a card the shopper named, `repeat` reads the list again, `cancel` stops,
   and `chat` answers a greeting or a question without touching the catalogue.
4. A search returns numbered cards. The narration lists them by number and ends
   by asking which card to book. Bulbul speaks it sentence by sentence while the
   words appear on screen in step with the audio.
5. "Card 2" creates a PayPal order. The app opens the approve page, and the
   capture call settles it. The order id and payer email come back to the screen.

## Running it

Backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest -q
```

Keys go in `backend/.env` (see `.env.example`): Spoonacular, PayPal sandbox,
Sarvam, optionally Groq, and Brevo if you want login emails. Without a key the
service still answers, using a small built-in catalogue and template narration.

App:

```bash
flutter create --platforms=android --org com.paygrow --project-name paygrow paygrow_app
cp -r frontend/lib paygrow_app/
cp frontend/pubspec.yaml paygrow_app/
flutter pub get
flutter run --dart-define=API_BASE=http://10.0.2.2:8000
```

The permissions the manifest needs are in `frontend/android_manifest_snippet.xml`.
Point `API_BASE` at the deployed backend when you are not on a laptop.

## What is simulated, and why

Prices and availability. Spoonacular's search endpoint returns no price at all,
and the details endpoint has a price whose unit nobody documents: the samples look
like cents and are often zero. Rather than invent a number quietly, every product
carries `price_source` (`simulated`, `api` or `api_assumed_cents`) and
`availability_source`, and the app prints it on the card. Stock does not exist in
that API in any form.

Payments run against the PayPal sandbox only.

Sessions live in memory unless a database URL is set, so history does not survive
a restart on the free tier. The Postgres path and the endpoints are there; it just
needs the connection string.

## Licence

MIT. See LICENSE.
