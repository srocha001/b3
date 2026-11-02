from datetime import date

from app.services.portfolio import calculate_position_pnl, combine_positions


def test_position_pnl_includes_fees():
    pnl = calculate_position_pnl(10, 20, 25, 5)

    assert pnl == {
        "invested": 205,
        "current_value": 250,
        "result": 45,
        "result_percent": 45 / 205 * 100,
    }


def test_combines_position_lots_using_weighted_average():
    positions = combine_positions(
        [
            {"ticker": "PETR4", "quantity": 10, "average_price": 10, "fees": 2, "purchased_at": date(2026, 1, 2)},
            {"ticker": "petr4", "quantity": 10, "average_price": 20, "fees": 3, "purchased_at": date(2026, 1, 5)},
        ]
    )

    assert len(positions) == 1
    assert positions[0]["quantity"] == 20
    assert positions[0]["average_price"] == 15
    assert positions[0]["fees"] == 5
    assert positions[0]["purchased_at"] == date(2026, 1, 5)
