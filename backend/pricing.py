"""Pure price normalization and historical-low helpers."""


CNY_RATES = {
    "CNY": 1, "USD": 7.2, "EUR": 7.8, "GBP": 9.1, "JPY": 0.049,
    "KRW": 0.0052, "HKD": 0.92, "TWD": 0.23, "BRL": 1.35,
    "RUB": 0.08, "TRY": 0.17, "ARS": 0.005,
}


def amount_int_to_cny(amount_int, currency):
    if amount_int is None:
        return None
    return (float(amount_int) / 100) * CNY_RATES.get(str(currency or "").upper(), 1)


def price_row_cny(row):
    item = dict(row)
    region = item.get("region")
    currency = item.get("currency") or ("CNY" if region == "CN" else "USD" if region in ("US", "ITAD-US") else "")
    return amount_int_to_cny(item.get("final"), currency)


def compare_historical_low(current_cny, low_cny, tolerance_cny):
    if current_cny is None or low_cny is None:
        return False
    # A newly observed lower price is also a historical low.  The tolerance
    # only applies above the stored low, to absorb minor currency rounding.
    return float(current_cny) <= float(low_cny) + float(tolerance_cny)


def effective_historical_low(itad_low_cny, observed_low_cny):
    candidates = []
    if itad_low_cny is not None:
        candidates.append((float(itad_low_cny), "itad"))
    if observed_low_cny is not None:
        candidates.append((float(observed_low_cny), "site_observed"))
    return min(candidates, default=(None, None), key=lambda item: item[0])


def cached_historical_low_match(current_cny, low_cny, source, discount_percent=0, observed_count=0, *, tolerance_cny):
    if not compare_historical_low(current_cny, low_cny, tolerance_cny):
        return False
    if source == "itad":
        return True
    return source == "site_observed" and int(observed_count or 0) >= 2 and int(discount_percent or 0) > 0
