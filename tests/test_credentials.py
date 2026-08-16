from __future__ import annotations

import pytest

import omd.credentials as credentials


def test_provider_env_var_uses_expected_provider_specific_names():
    assert credentials.api_key_env_var("openai") == "OPENAI_API_KEY"
    assert credentials.api_key_env_var("anthropic") == "ANTHROPIC_API_KEY"
    assert credentials.api_key_env_var("deepseek") == "DEEPSEEK_API_KEY"


def test_provider_validation_rejects_unknown_provider_name():
    with pytest.raises(ValueError, match="provider"):
        credentials.api_key_env_var("openrouter")


def test_load_api_key_prefers_environment_without_touching_keychain():
    def fail_reader(*_args):
        raise AssertionError("keychain runner should not be called when env override exists")

    value = credentials.load_api_key(
        "openai",
        env={"OPENAI_API_KEY": "env-secret"},
        platform="linux",
        native_reader=fail_reader,
    )

    assert value == "env-secret"


def test_load_api_key_strips_accidental_environment_whitespace():
    def fail_reader(*_args):
        raise AssertionError("keychain runner should not be called")

    value = credentials.load_api_key(
        "openai",
        env={"OPENAI_API_KEY": "  env-secret\n"},
        platform="linux",
        native_reader=fail_reader,
    )

    assert value == "env-secret"


def test_load_api_key_reads_from_keychain_when_environment_is_absent():
    calls = []

    def fake_native_reader(service, account):
        calls.append((service, account))
        return "kc-secret\n"

    value = credentials.load_api_key(
        "anthropic",
        env={},
        platform="darwin",
        native_reader=fake_native_reader,
    )

    assert value == "kc-secret"
    assert calls == [("omd/anthropic/api-key", "ANTHROPIC_API_KEY")]


def test_store_api_key_uses_native_keychain_writer_without_subprocess_argv():
    calls = []

    def fake_native_writer(service, account, secret):
        calls.append((service, account, secret))

    credentials.store_api_key(
        "deepseek",
        "top-secret-value",
        platform="darwin",
        native_writer=fake_native_writer,
    )

    assert calls == [
        (
            "omd/deepseek/api-key",
            "DEEPSEEK_API_KEY",
            "top-secret-value",
        )
    ]


@pytest.mark.parametrize("secret", ["sk-first\nInjected: value", "sk-null\x00value"])
def test_store_api_key_rejects_embedded_control_characters_before_keychain(secret):
    calls = []

    with pytest.raises(ValueError, match="control characters"):
        credentials.store_api_key(
            "openai",
            secret,
            platform="darwin",
            native_writer=lambda *args: calls.append(args),
        )

    assert calls == []


def test_load_api_key_rejects_embedded_control_characters_from_environment():
    calls = []

    with pytest.raises(ValueError, match="control characters"):
        credentials.load_api_key(
            "openai",
            env={"OPENAI_API_KEY": "sk-first\nInjected: value"},
            platform="darwin",
            native_reader=lambda *args: calls.append(args),
        )

    assert calls == []


def test_delete_api_key_removes_provider_entry_from_keychain():
    calls = []

    def fake_native_deleter(service, account):
        calls.append((service, account))

    credentials.delete_api_key(
        "openai",
        platform="darwin",
        native_deleter=fake_native_deleter,
    )

    assert calls == [("omd/openai/api-key", "OPENAI_API_KEY")]


def test_load_api_key_raises_explicit_capability_error_when_keychain_is_unavailable():
    with pytest.raises(credentials.CredentialCapabilityError, match="macOS Keychain"):
        credentials.load_api_key(
            "openai",
            env={},
            platform="linux",
            native_reader=lambda *_args: None,
        )


def test_store_api_key_raises_explicit_capability_error_when_security_framework_is_missing():
    def missing_framework(*_args):
        raise credentials.CredentialCapabilityError("macOS Keychain Security.framework unavailable")

    with pytest.raises(credentials.CredentialCapabilityError, match="macOS Keychain"):
        credentials.store_api_key(
            "openai",
            "secret",
            platform="darwin",
            native_writer=missing_framework,
        )


def test_load_api_key_raises_not_found_for_missing_keychain_entry():
    def missing_reader(*_args):
        raise credentials.CredentialNotFoundError("No stored credential exists for provider 'openai'.")

    with pytest.raises(credentials.CredentialNotFoundError, match="openai"):
        credentials.load_api_key(
            "openai",
            env={},
            platform="darwin",
            native_reader=missing_reader,
        )


def test_store_api_key_never_surfaces_secret_in_operation_errors():
    def failing_native_writer(*_args):
        raise OSError("leaked-secret")

    with pytest.raises(credentials.CredentialOperationError) as exc_info:
        credentials.store_api_key(
            "openai",
            "leaked-secret",
            platform="darwin",
            native_writer=failing_native_writer,
        )

    assert "leaked-secret" not in str(exc_info.value)


def test_native_keychain_status_errors_do_not_surface_secret():
    def failing_native_writer(*_args):
        raise credentials.CredentialOperationError("Keychain write failed with status -1")

    with pytest.raises(credentials.CredentialOperationError) as exc_info:
        credentials.store_api_key(
            "openai",
            "leaked-secret",
            platform="darwin",
            native_writer=failing_native_writer,
        )

    assert "leaked-secret" not in str(exc_info.value)
