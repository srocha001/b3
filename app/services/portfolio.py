def calculate_position_pnl(
    quantity: float, average_price: float, current_price: float, fees: float = 0
) -> dict[str, float]:
    invested = quantity * average_price + fees
    current_value = quantity * current_price
    result = current_value - invested
    return {
        "invested": invested,
        "current_value": current_value,
        "result": result,
        "result_percent": (result / invested * 100) if invested else 0.0,
    }


def combine_positions(rows: list[dict]) -> list[dict]:
    combined: dict[str, dict] = {}
    for row in rows:
        ticker = row["ticker"].upper()
        item = combined.setdefault(
            ticker,
            {
                "ticker": ticker,
                "quantity": 0.0,
                "cost": 0.0,
                "fees": 0.0,
                "purchased_at": row["purchased_at"],
            },
        )
        item["quantity"] += row["quantity"]
        item["cost"] += row["quantity"] * row["average_price"]
        item["fees"] += row.get("fees", 0)
        item["purchased_at"] = max(item["purchased_at"], row["purchased_at"])
    return [
        {
            **item,
            "average_price": item["cost"] / item["quantity"]
            if item["quantity"]
            else 0,
        }
        for item in combined.values()
    ]
