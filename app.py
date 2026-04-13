import asyncio
import os
import re
from typing import Any

from flask import Flask, jsonify, render_template, request

try:
    import opengradient as og
except Exception:
    og = None


app = Flask(__name__)

SYSTEM_PROMPT = (
    "You are IntentGuard, a concise on-chain safety copilot. "
    "Explain top risks, what not to sign, and a safer alternative plan."
)

OG_SDK_MODEL = os.getenv("OG_SDK_MODEL", "GEMINI_2_5_FLASH")
OG_SETTLEMENT_MODE = os.getenv("OG_SETTLEMENT_MODE", "PRIVATE").upper()
OG_APPROVAL_OPG_AMOUNT = float(os.getenv("OG_APPROVAL_OPG_AMOUNT", "5"))


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _resolve_og_model():
    if og is None:
        raise RuntimeError("opengradient package is not available")
    model = getattr(og.TEE_LLM, OG_SDK_MODEL, None)
    if model is None:
        raise RuntimeError(f"Unknown OG_SDK_MODEL '{OG_SDK_MODEL}'")
    return model


def _resolve_settlement_mode():
    if og is None:
        raise RuntimeError("opengradient package is not available")
    mode = getattr(og.x402SettlementMode, OG_SETTLEMENT_MODE, None)
    if mode is None:
        raise RuntimeError(f"Unknown OG_SETTLEMENT_MODE '{OG_SETTLEMENT_MODE}'")
    return mode


def _ensure_approval_once(llm):
    errors = []
    for kwargs in (
        {"min_allowance": OG_APPROVAL_OPG_AMOUNT},
        {"opg_amount": OG_APPROVAL_OPG_AMOUNT},
        {},
    ):
        try:
            llm.ensure_opg_approval(**kwargs)
            return
        except TypeError as exc:
            errors.append(str(exc))
    raise RuntimeError("ensure_opg_approval failed for all known signatures: " + " | ".join(errors))


async def _call_og_intent_explainer(intent: str, chain: str, wallet: str, context: str) -> str:
    private_key = os.getenv("OG_PRIVATE_KEY")
    if og is None or not private_key:
        raise RuntimeError("OG SDK or OG_PRIVATE_KEY is not configured")

    llm = og.LLM(private_key=private_key)
    _ensure_approval_once(llm)
    prompt = (
        f"Intent: {intent}\n"
        f"Chain: {chain}\n"
        f"Wallet: {wallet or 'N/A'}\n"
        f"Context: {context or 'N/A'}\n\n"
        "Return 4 short sections:\n"
        "1) Risk summary\n"
        "2) Red flags\n"
        "3) Do-not-sign checklist\n"
        "4) Safer alternative plan"
    )
    result = await llm.chat(
        model=_resolve_og_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=350,
        x402_settlement_mode=_resolve_settlement_mode(),
    )
    content = ""
    if isinstance(result.chat_output, dict):
        content = (result.chat_output.get("content") or "").strip()
    if not content:
        raise RuntimeError("Empty OG response")
    return content


def _simple_risk_engine(intent: str, context: str) -> dict[str, Any]:
    text = f"{intent}\n{context}".lower()
    score = 18
    factors: list[str] = []

    rules = [
        (r"\b(unlimited|max|infinite)\b", 22, "Unlimited approval pattern detected"),
        (r"\b(bridge|cross[- ]?chain)\b", 16, "Bridge operation adds settlement and routing risk"),
        (r"\b(lp|liquidity|pool)\b", 14, "LP actions can expose impermanent loss and rug vectors"),
        (r"\b(leverage|margin|borrow)\b", 18, "Leverage path increases liquidation risk"),
        (r"\b(new token|meme|low cap)\b", 15, "Low-liquidity token profile detected"),
        (r"\b(admin|owner|proxy upgrade)\b", 12, "Admin/proxy control risk mention"),
        (r"\b(seed|private key|mnemonic)\b", 35, "Critical secret handling risk"),
    ]

    for pattern, delta, reason in rules:
        if re.search(pattern, text):
            score += delta
            factors.append(reason)

    score = max(0, min(100, score))
    if score >= 75:
        tier = "critical"
    elif score >= 55:
        tier = "high"
    elif score >= 35:
        tier = "medium"
    else:
        tier = "low"

    if not factors:
        factors.append("No major explicit red flags in text; still verify token and spender addresses")

    do_not_sign = [
        "Any transaction with different spender than expected",
        "Unlimited token approval for unknown contracts",
        "Blind signature prompts without human-readable details",
    ]
    safer_plan = [
        "Start with a small test transaction",
        "Use exact amount approvals (not unlimited)",
        "Verify contract and spender in explorer before signing",
    ]

    return {
        "safety_score": 100 - score,
        "risk_score": score,
        "risk_tier": tier,
        "top_factors": factors[:4],
        "do_not_sign_list": do_not_sign,
        "safer_plan": safer_plan,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "product": "IntentGuard OG Studio",
            "has_og_private_key": bool(os.getenv("OG_PRIVATE_KEY")),
            "og_sdk_available": og is not None,
            "og_sdk_model": OG_SDK_MODEL,
            "og_settlement_mode": OG_SETTLEMENT_MODE,
        }
    )


@app.post("/api/intent/analyze")
def analyze_intent():
    data = request.get_json(silent=True) or {}
    intent = (data.get("intent") or "").strip()
    chain = (data.get("chain") or "Base Sepolia").strip()
    wallet = (data.get("wallet") or "").strip()
    context = (data.get("context") or "").strip()

    if not intent:
        return jsonify({"error": "intent is required"}), 400

    heuristic = _simple_risk_engine(intent, context)

    ai_report = ""
    provider = "heuristic_only"
    try:
        ai_report = _run_async(_call_og_intent_explainer(intent, chain, wallet, context))
        provider = "opengradient_sdk"
    except Exception:
        ai_report = (
            "OG explainer is currently unavailable. Heuristic safety report is shown. "
            "Try again in a minute for a full TEE explanation."
        )

    return jsonify(
        {
            "ok": True,
            "provider": provider,
            "intent": intent,
            "chain": chain,
            "wallet": wallet,
            "result": {
                **heuristic,
                "ai_report": ai_report,
                "proof_note": "TEE proof/tx can be surfaced here when provider is opengradient_sdk",
            },
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
