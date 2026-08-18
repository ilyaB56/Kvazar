def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires running API + DB (docker compose up)")
