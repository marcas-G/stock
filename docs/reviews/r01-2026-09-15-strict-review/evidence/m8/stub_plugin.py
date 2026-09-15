"""Pytest plugin: patch projection authorities with stubs to prove tests kill them."""
import pytest


def pytest_configure(config):
    import factorlab.app.backtest.orders as orders
    import factorlab.app.backtest.fills as fills

    def stub_sell(rule, *, holding_quantity, max_quantity):
        return max_quantity          # oversell stub

    def stub_buy(rule, max_quantity):
        return max_quantity          # ignores lot rules

    orders.project_sell_quantity = stub_sell
    orders.is_valid_sell_quantity = lambda *a, **k: True
    fills.project_buy_quantity = stub_buy
