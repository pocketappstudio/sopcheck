"""Per-request token/cost metering for OpenAI-compatible calls (contextvar, so concurrent requests don't mix).
Prices are list-price estimates in USD per 1M tokens; override with LLM_PRICE_IN / LLM_PRICE_OUT (for LLM_MODEL)."""
import contextvars, os

PRICES = {  # (input, output) USD per 1M tokens
    "gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0), "text-embedding-3-large": (0.13, 0.0),
}
_meter: contextvars.ContextVar = contextvars.ContextVar("llm_usage", default=None)


def start() -> dict:
    m = {"calls": 0, "by_model": {}}
    _meter.set(m)
    return m


def _price(model: str):
    from . import config
    if model == config.LLM_MODEL and os.getenv("LLM_PRICE_IN"):
        return float(os.getenv("LLM_PRICE_IN")), float(os.getenv("LLM_PRICE_OUT", "0"))
    base = next((k for k in sorted(PRICES, key=len, reverse=True) if model.startswith(k)), None)
    return PRICES.get(base) if base else None


def record(model: str, usage) -> None:
    m = _meter.get()
    if m is None or usage is None:
        return
    pin = getattr(usage, "prompt_tokens", 0) or 0
    pout = getattr(usage, "completion_tokens", 0) or 0
    b = m["by_model"].setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    b["calls"] += 1; b["prompt_tokens"] += pin; b["completion_tokens"] += pout
    m["calls"] += 1


def summary(m: dict | None) -> dict | None:
    if not m or not m["calls"]:
        return None
    cost, unknown = 0.0, []
    for model, b in m["by_model"].items():
        p = _price(model)
        if p is None:
            unknown.append(model); continue
        cost += b["prompt_tokens"] / 1e6 * p[0] + b["completion_tokens"] / 1e6 * p[1]
    return {**m, "prompt_tokens": sum(b["prompt_tokens"] for b in m["by_model"].values()),
            "completion_tokens": sum(b["completion_tokens"] for b in m["by_model"].values()),
            "est_cost_usd": round(cost, 5), "unpriced_models": unknown}
