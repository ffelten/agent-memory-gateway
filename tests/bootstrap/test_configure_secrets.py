import json

import pytest


DEPLOYMENT = {"gateway_config_secret_arn": "gateway-config-test", "clickhouse_secret_arn": "clickhouse-test"}


class SecretsClient:
    def __init__(self):
        self.gateway = {
            "admin_token_sha256": "a" * 64,
            "report_base_url": "https://gateway.example.test",
            "unrelated": {"keep": True},
            "senso": {"base_url": "https://apiv2.senso.ai/api/v1", "api_key": "old-key", "folder_id": "old-folder"},
        }
        self.reads = []
        self.writes = []

    def get_secret_value(self, **kwargs):
        self.reads.append(kwargs)
        return {"SecretString": json.dumps(self.gateway)}

    def put_secret_value(self, **kwargs):
        self.writes.append(kwargs)
        return {"VersionId": "test-version"}


def test_env_parser_treats_shell_expressions_as_literal_data(tmp_path):
    from infra.configure_secrets import read_env

    path = tmp_path / ".env"
    path.write_text("# local secrets\nexport SENSO_API_KEY='$(touch /tmp/must-not-run)'\nSENSO_FOLDER_ID=${OTHER}\nCLICKHOUSE_PASSWORD=\"literal#password\" # comment\nEMPTY=\n")
    assert read_env(path) == {
        "SENSO_API_KEY": "$(touch /tmp/must-not-run)",
        "SENSO_FOLDER_ID": "${OTHER}",
        "CLICKHOUSE_PASSWORD": "literal#password",
        "EMPTY": "",
    }


@pytest.mark.parametrize("content", ["KEY=first\nKEY=second", "PRIVATE_SECRET_WITHOUT_EQUALS", 'KEY="unclosed'])
def test_invalid_env_lines_have_bounded_errors(tmp_path, content):
    from infra.configure_secrets import ConfigurationError, read_env

    path = tmp_path / ".env"
    path.write_text(content)
    with pytest.raises(ConfigurationError) as raised:
        read_env(path)
    assert content not in str(raised.value)


def test_partial_credentials_do_not_read_or_write_secrets_and_report_names_only():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"SENSO_API_KEY": "private-senso", "CLICKHOUSE_PASSWORD": "private-clickhouse"}, DEPLOYMENT, client=client)
    assert result["senso"] == {"status": "missing", "missing": ["SENSO_FOLDER_ID"]}
    assert "CLICKHOUSE_PASSWORD" not in result["clickhouse"]["missing"]
    assert client.reads == [] and client.writes == []
    assert "private-senso" not in json.dumps(result)
    assert "private-clickhouse" not in json.dumps(result)


def test_complete_senso_updates_only_nested_senso_configuration():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"SENSO_API_KEY": "new-private-key", "SENSO_FOLDER_ID": "new-folder"}, DEPLOYMENT, client=client)
    assert result["senso"]["status"] == "updated"
    assert len(client.writes) == 1
    saved = json.loads(client.writes[0]["SecretString"])
    assert saved["admin_token_sha256"] == client.gateway["admin_token_sha256"]
    assert saved["report_base_url"] == client.gateway["report_base_url"]
    assert saved["unrelated"] == {"keep": True}
    assert saved["senso"] == {"base_url": "https://apiv2.senso.ai/api/v1", "api_key": "new-private-key", "folder_id": "new-folder"}


def test_clickhouse_host_and_user_aliases_build_complete_https_config():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"CLICKHOUSE_HOST": "cluster.example.test", "CLICKHOUSE_USER": "writer", "CLICKHOUSE_PASSWORD": "private-password"}, DEPLOYMENT, client=client)
    assert result["clickhouse"]["status"] == "updated"
    assert client.reads == []
    assert json.loads(client.writes[0]["SecretString"]) == {"url": "https://cluster.example.test:8443", "username": "writer", "password": "private-password", "database": "default"}


def test_clickhouse_url_and_username_take_precedence_over_aliases():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"CLICKHOUSE_URL": "https://cluster.example.test:9440/", "CLICKHOUSE_HOST": "unused", "CLICKHOUSE_USERNAME": "writer", "CLICKHOUSE_USER": "unused", "CLICKHOUSE_PASSWORD": "private-password", "CLICKHOUSE_DATABASE": "gateway_demo"}, DEPLOYMENT, client=client)
    assert result["clickhouse"]["status"] == "updated"
    saved = json.loads(client.writes[0]["SecretString"])
    assert saved["url"] == "https://cluster.example.test:9440/"
    assert saved["username"] == "writer" and saved["database"] == "gateway_demo"


def test_clickhouse_uses_explicit_https_port_and_ignores_cloud_api_keys():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"CLICKHOUSE_HOST": "cluster.example.test", "CLICKHOUSE_HTTPS_PORT": "9443", "CLICKHOUSE_NATIVE_PORT": "9000", "CLICKHOUSE_USER": "writer", "CLICKHOUSE_PASSWORD": "private-password", "CLICKHOUSE_CLOUD_API_KEY": "private-cloud-key", "CLICKHOUSE_CLOUD_API_SECRET": "private-cloud-secret"}, DEPLOYMENT, client=client)
    assert result["clickhouse"]["status"] == "updated"
    saved = json.loads(client.writes[0]["SecretString"])
    assert saved == {"url": "https://cluster.example.test:9443", "username": "writer", "password": "private-password", "database": "default"}


@pytest.mark.parametrize("port", ["0", "65536", "https", "8443/private"])
def test_invalid_https_port_does_not_write_credentials(port):
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"CLICKHOUSE_HOST": "cluster.example.test", "CLICKHOUSE_HTTPS_PORT": port, "CLICKHOUSE_USER": "writer", "CLICKHOUSE_PASSWORD": "private-password"}, DEPLOYMENT, client=client)
    assert result["clickhouse"]["status"] == "invalid"
    assert client.writes == []


def test_comment_only_empty_value_is_missing_not_a_credential(tmp_path):
    from infra.configure_secrets import read_env

    path = tmp_path / ".env"
    path.write_text("CLICKHOUSE_PASSWORD= # fill this in\n")
    assert read_env(path)["CLICKHOUSE_PASSWORD"] == ""


def test_unquoted_leading_hash_password_is_preserved_when_not_a_comment(tmp_path):
    from infra.configure_secrets import read_env

    path = tmp_path / ".env"
    path.write_text("CLICKHOUSE_PASSWORD=#literal-password\n")
    assert read_env(path)["CLICKHOUSE_PASSWORD"] == "#literal-password"


def test_invalid_clickhouse_config_is_not_written_or_reported_verbatim():
    from infra.configure_secrets import configure_integrations

    client = SecretsClient()
    result = configure_integrations({"CLICKHOUSE_URL": "http://private-host.invalid/?password=private-value", "CLICKHOUSE_USER": "writer", "CLICKHOUSE_PASSWORD": "private-password"}, DEPLOYMENT, client=client)
    assert result["clickhouse"]["status"] == "invalid"
    assert client.reads == [] and client.writes == []
    assert "private-" not in json.dumps(result)


def test_provider_errors_are_bounded_and_do_not_erase_config(capsys, caplog):
    from infra.configure_secrets import configure_integrations

    class BrokenClient(SecretsClient):
        def get_secret_value(self, **kwargs):
            raise RuntimeError("private-provider-error-password")

    client = BrokenClient()
    result = configure_integrations({"SENSO_API_KEY": "private-senso", "SENSO_FOLDER_ID": "folder"}, DEPLOYMENT, client=client)
    assert result["senso"]["status"] == "error"
    assert client.writes == []
    assert "private-" not in json.dumps(result) + caplog.text + str(capsys.readouterr())


def test_invalid_existing_gateway_json_is_never_replaced():
    from infra.configure_secrets import configure_integrations

    class InvalidClient(SecretsClient):
        def get_secret_value(self, **kwargs):
            return {"SecretString": "private malformed existing configuration"}

    client = InvalidClient()
    result = configure_integrations({"SENSO_API_KEY": "private-senso", "SENSO_FOLDER_ID": "folder"}, DEPLOYMENT, client=client)
    assert result["senso"]["status"] == "error"
    assert client.writes == []


def test_cli_missing_configuration_reports_names_without_creating_client(tmp_path, capsys):
    from infra.configure_secrets import main

    env = tmp_path / ".env"
    env.write_text("SENSO_API_KEY=private-secret-value\n")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(json.dumps(DEPLOYMENT))
    assert main(["--env-file", str(env), "--deployment-file", str(deployment)], client_factory=lambda: (_ for _ in ()).throw(AssertionError("SDK must not be called"))) == 2
    output = capsys.readouterr()
    assert "SENSO_FOLDER_ID" in output.out
    assert "private-secret-value" not in str(output)


def test_successful_cli_outputs_status_only(tmp_path, capsys):
    from infra.configure_secrets import main

    env = tmp_path / ".env"
    env.write_text("SENSO_API_KEY=private-senso-value\nSENSO_FOLDER_ID=private-folder\nCLICKHOUSE_HOST=private-host.example.test\nCLICKHOUSE_USER=private-user\nCLICKHOUSE_PASSWORD=private-password\n")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(json.dumps(DEPLOYMENT))
    client = SecretsClient()
    assert main(["--env-file", str(env), "--deployment-file", str(deployment)], client=client) == 0
    output = capsys.readouterr()
    assert output.out == "senso: updated\nclickhouse: updated\n"
    assert output.err == ""
    assert len(client.writes) == 2
