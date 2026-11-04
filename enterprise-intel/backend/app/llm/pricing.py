# USD per 1M tokens (input, output). Estimates for the dashboard; verify against each provider's pricing page.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (1.0, 5.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "gemini-2.5-flash": (0.30, 2.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)
