"""Network configuration is writable without returning proxy credentials."""

from hoshimi.core.net.client import NetworkClient


def test_network_settings_are_validated_and_redacted(client, services):
    result = client.patch("/api/v1/settings", json={"network": {"mode": "proxy"}})
    assert result.status_code == 400
    result = client.patch("/api/v1/settings", json={"network": {"mode": "proxy", "proxy": "http://user:secret@proxy.test:8080"}})
    assert result.status_code == 200
    assert result.json()["network"] == {"mode": "proxy", "proxy_configured": True, "system_proxy": False}
    assert "secret" not in result.text
    assert services.settings.network.proxy == "http://user:secret@proxy.test:8080"
    result = client.patch("/api/v1/settings", json={"providers": {"fake": {"options": {"network": {"mode": "direct"}}}}})
    assert result.status_code == 200
    assert next(row for row in result.json()["providers"] if row["id"] == "fake")["network"]["mode"] == "direct"
    assert client.get("/api/v1/network/diagnostics").status_code == 200


def test_global_route_change_keeps_account_and_client_state(client, services):
    from hoshimi.core.modelsdev import MODELSDEV

    client.patch("/api/v1/settings", json={"providers": {"cline": {"accounts": [{"id": "a", "access_token": "test"}]}}})
    provider = services.registry.providers["cline"]
    account = provider.accounts[0]
    account.needs_login = True
    result = client.patch("/api/v1/settings", json={"network": {"mode": "direct"}})
    assert result.status_code == 200
    assert services.registry.providers["cline"] is provider
    assert provider.accounts[0] is account and account.needs_login
    assert isinstance(provider.client, NetworkClient)
    assert provider.client.policy.mode == "direct"
    assert MODELSDEV.network.mode == "direct"
    result = client.patch("/api/v1/settings", json={"providers": {"cline": {"options": {"network": {"mode": "proxy", "proxy": "http://proxy.test:8080"}}}}})
    assert result.status_code == 200
    assert services.registry.providers["cline"] is provider
    assert provider.accounts[0] is account and account.needs_login
    assert provider.client.policy.proxy == "http://proxy.test:8080"


def test_cli_masks_global_and_provider_proxy_credentials():
    from hoshimi.cli.commands.config import _mask

    raw = {"network": {"mode": "proxy", "proxy": "http://user:secret@proxy.test"},
           "providers": {"cline": {"options": {"network": {"proxy": "socks5://user:secret@other.test"}}}}}
    masked = _mask(raw)
    assert masked["network"]["proxy"] == "***"
    assert masked["providers"]["cline"]["options"]["network"]["proxy"] == "***"
    assert raw["network"]["proxy"].startswith("http://")
