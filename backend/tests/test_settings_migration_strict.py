from settings_migration import migrate_settings


# Legacy-to-strict settings migration preserves budget and drops legacy objective knobs
def test_migrate_settings_preserves_budget_and_returns_strict_model():
    legacy = {
        "asset": "ETH",
        "budget": 321.25,
        "objective": "maximum_upside",
        "min_profit": 0.25,
        "max_loss": 5,
        "max_deployment": 100,
        "latency_ms": 123,
    }

    migrated = migrate_settings(legacy)
    payload = migrated.model_dump()

    assert payload["strategy"] == "strict_arbitrage"
    assert payload["budget"] == 321.25
    assert payload["asset"] == "ETH"
    assert payload["max_deployment"] == 100
    assert payload["latency_ms"] == 123
    assert "objective" not in payload
    assert "min_profit" not in payload
