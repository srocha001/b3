from app.signals.engine import backtest, evaluate_fundamentals, evaluate_signal


def test_signal_explains_each_rule_and_score():
    history = [
        {"close": 10 + index * 0.1, "volume": 2_000_000}
        for index in range(220)
    ]
    quote = {
        "ticker": "TEST3",
        "price": 35,
        "pe_ratio": 10,
        "price_to_book": 1.5,
        "dividend_yield": 0.05,
        "roe": 0.12,
        "net_debt_to_ebitda": 2,
        "average_volume": 2_000_000,
    }

    result = evaluate_signal(quote, history)

    assert result["signal"] == "COMPRA"
    assert result["score"] >= 70
    assert len(result["criteria"]) == 12
    assert all("passed" in criterion and "value" in criterion for criterion in result["criteria"])
    assert result["data"]["technical"]["rsi_14"] == 100


def test_missing_fundamentals_are_not_marked_as_favorable():
    evaluation = evaluate_fundamentals({"pe_ratio": None})

    assert evaluation["status"] == "Dados insuficientes"
    assert any(item["passed"] is None for item in evaluation["criteria"])


def test_backtest_reports_insufficient_history():
    result = backtest([{"close": 10 + index} for index in range(24)])

    assert result["error"]


def test_backtest_uses_configured_technical_rules():
    history = [
        {
            "close": 20 + index * 0.05 + (index % 7) * 0.1,
            "volume": 2_000_000 + (index % 4) * 100_000,
        }
        for index in range(260)
    ]

    result = backtest(history)

    assert result["scope"].startswith("Backtest técnico")
    assert result["trades"] > 0
    assert result["hits"] + result["errors"] == result["trades"]
