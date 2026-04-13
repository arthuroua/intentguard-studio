# IntentGuard OG Studio

New standalone OpenGradient-style web app for intent risk simulation.

## What it does
- Accepts a user on-chain intent (swap/bridge/LP/etc.)
- Computes a heuristic risk score and safety tier
- Tries to generate an OpenGradient SDK explanation when `OG_PRIVATE_KEY` is set
- Falls back to local explanation when OG is unavailable

## Run locally
```bash
pip install -r requirements.txt
python app.py
```

## Railway variables (optional but recommended)
- `OG_PRIVATE_KEY=0x...`
- `OG_SDK_MODEL=GEMINI_2_5_FLASH`
- `OG_SETTLEMENT_MODE=PRIVATE`
- `OG_APPROVAL_OPG_AMOUNT=5`

## Endpoints
- `GET /`
- `GET /health`
- `POST /api/intent/analyze`
