import json
import math
from datetime import datetime, timezone
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")


def load_rules() -> dict:
    with RULES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def _rsi(values: list[float]) -> float | None:
    if len(values) < 15:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = sum(max(change, 0) for change in changes[:14]) / 14
    losses = sum(max(-change, 0) for change in changes[:14]) / 14
    for change in changes[14:]:
        gains = (gains * 13 + max(change, 0)) / 14
        losses = (losses * 13 + max(-change, 0)) / 14
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for point in values[period:]:
        value = (point - value) * multiplier + value
    return value


def _macd_positive(values: list[float]) -> bool | None:
    fast, slow = _ema(values, 12), _ema(values, 26)
    return fast > slow if fast is not None and slow is not None else None


def evaluate_signal(quote: dict, history: list[dict], rules: dict | None = None) -> dict:
    rules = rules or load_rules()
    prices = [float(point["close"]) for point in history if point.get("close") is not None]
    volumes = [float(point["volume"]) for point in history if point.get("volume") is not None]
    facts = {
        "pe_ratio": quote.get("pe_ratio"),
        "price_to_book": quote.get("price_to_book"),
        "dividend_yield": quote.get("dividend_yield"),
        "roe": quote.get("roe"),
        "net_debt_to_ebitda": quote.get("net_debt_to_ebitda"),
        "average_volume": quote.get("average_volume"),
    }
    price = float(quote["price"])
    technical = {
        "above_sma20": price > _sma(prices, 20) if _sma(prices, 20) is not None else None,
        "above_sma50": price > _sma(prices, 50) if _sma(prices, 50) is not None else None,
        "above_sma200": price > _sma(prices, 200) if _sma(prices, 200) is not None else None,
        "rsi_14": _rsi(prices),
        "macd_positive": _macd_positive(prices),
        "relative_volume": (
            volumes[-1] > sum(volumes[-21:-1]) / 20
            if len(volumes) >= 21
            else None
        ),
    }
    results = []
    for category in ("fundamental", "technical"):
        for rule in rules[category]:
            value = facts.get(rule["key"]) if category == "fundamental" else technical.get(rule["key"])
            if category == "fundamental":
                passed = _test(value, rule)
            elif value is None:
                passed = None
            elif rule["key"] == "rsi_14":
                passed = 30 <= value <= 70
            else:
                passed = bool(value)
            results.append(
                {
                    "label": rule["label"],
                    "passed": passed,
                    "value": value,
                    "weight": rule["weight"],
                    "category": category,
                }
            )
    weight_total = sum(rule["weight"] for group in ("fundamental", "technical") for rule in rules[group])
    score = round(
        sum(item["weight"] for item in results if item["passed"] is True)
        / weight_total
        * 100
    ) if weight_total else 0
    thresholds = rules["thresholds"]
    signal = "COMPRA" if score >= thresholds["buy"] else "ATENÇÃO" if score < thresholds["attention"] else "NEUTRO"
    return {
        "ticker": quote.get("ticker"),
        "signal": signal,
        "score": score,
        "criteria": results,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "data": {"price": price, "fundamentals": facts, "technical": technical},
    }


def evaluate_fundamentals(quote: dict, rules: dict | None = None) -> dict:
    rules = rules or load_rules()
    criteria = []
    for rule in rules["fundamental"]:
        value = quote.get(rule["key"])
        passed = _test(value, rule)
        criteria.append(
            {
                "name": rule["label"],
                "value": value,
                "passed": passed,
                "available": value is not None,
            }
        )
    status = (
        "Favorável"
        if all(item["passed"] is True for item in criteria)
        else "Dados insuficientes"
        if any(item["passed"] is None for item in criteria)
        else "Fundamentos em atenção"
    )
    return {"criteria": criteria, "status": status}


def _test(value, rule: dict) -> bool | None:
    if value is None:
        return None
    if rule["operator"] == "between":
        return rule["min"] <= value <= rule["max"]
    if rule["operator"] == "gte":
        return value >= rule["value"]
    if rule["operator"] == "lte":
        return value <= rule["value"]
    raise ValueError(f"Operador não reconhecido: {rule['operator']}")


def backtest(history: list[dict], benchmark: list[dict] | None = None) -> dict:
    clean_history = [point for point in history if point.get("close") is not None]
    closes = [float(point["close"]) for point in clean_history]
    if len(closes) < 25:
        return {"error": "Histórico insuficiente: são necessários ao menos 25 fechamentos."}
    rules = load_rules()
    threshold = rules["thresholds"]["buy"]
    hits = errors = trades = 0
    strategy_returns = []
    for index in range(20, len(closes) - 5):
        window = clean_history[max(0, index - 199) : index + 1]
        technical_quote = {
            "ticker": "BACKTEST",
            "price": closes[index],
            "pe_ratio": None,
            "price_to_book": None,
            "dividend_yield": None,
            "roe": None,
            "net_debt_to_ebitda": None,
            "average_volume": None,
        }
        technical = evaluate_signal(technical_quote, window, rules)
        criteria = [item for item in technical["criteria"] if item["category"] == "technical" and item["passed"] is not None]
        available_weight = sum(item["weight"] for item in criteria)
        technical_score = (
            sum(item["weight"] for item in criteria if item["passed"]) / available_weight * 100
            if available_weight
            else 0
        )
        if technical_score < threshold:
            continue
        trades += 1
        change = closes[index + 5] / closes[index] - 1
        strategy_returns.append(change)
        if change > 0:
            hits += 1
        else:
            errors += 1
    total_return = (math.prod(1 + change for change in strategy_returns) - 1) * 100
    benchmark_return = None
    if benchmark:
        benchmark_closes = [float(point["close"]) for point in benchmark if point.get("close")]
        if len(benchmark_closes) > 1:
            benchmark_return = (benchmark_closes[-1] / benchmark_closes[0] - 1) * 100
    return {
        "trades": trades,
        "hits": hits,
        "errors": errors,
        "hit_rate": hits / trades * 100 if trades else 0,
        "strategy_return_percent": total_return,
        "benchmark_return_percent": benchmark_return,
        "scope": "Backtest técnico: fundamentos históricos indisponíveis na fonte.",
    }
