def format_usd(value: float) -> str:
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= limit:
            return f"${value / limit:.1f}{suffix}"
    return f"${value:.0f}"
