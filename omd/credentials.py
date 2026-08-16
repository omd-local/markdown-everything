"""Provider-scoped API key access via environment overrides and macOS Keychain.

Public API:
- ``api_key_env_var(provider)`` returns the supported provider env var name.
- ``load_api_key(provider, ...)`` reads from the environment first, then macOS Keychain.
- ``store_api_key(provider, secret, ...)`` writes through macOS Security.framework.
- ``delete_api_key(provider, ...)`` deletes the provider entry from macOS Keychain.

This module intentionally does not log, serialize, or persist credentials anywhere
other than the user's macOS Keychain.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
import os
import sys

__all__ = [
    "CredentialCapabilityError",
    "CredentialNotFoundError",
    "CredentialOperationError",
    "api_key_env_var",
    "delete_api_key",
    "load_api_key",
    "store_api_key",
]


_PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
NativeReader = Callable[[str, str], str]
NativeWriter = Callable[[str, str, str], None]
NativeDeleter = Callable[[str, str], None]

_SECURITY_FRAMEWORK = "/System/Library/Frameworks/Security.framework/Security"
_CORE_FOUNDATION_FRAMEWORK = (
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
_ERR_SEC_DUPLICATE_ITEM = -25299
_ERR_SEC_ITEM_NOT_FOUND = -25300


class CredentialError(RuntimeError):
    """Base class for credential boundary failures."""


class CredentialCapabilityError(CredentialError):
    """Raised when the macOS Keychain capability is unavailable."""


class CredentialNotFoundError(CredentialError):
    """Raised when a provider has no stored credential."""


class CredentialOperationError(CredentialError):
    """Raised when a Keychain operation fails without exposing secret material."""


def api_key_env_var(provider: str) -> str:
    """Return the supported env var name for a credential provider."""
    normalized = _normalize_provider(provider)
    return _PROVIDER_ENV_VARS[normalized]


def load_api_key(
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    native_reader: NativeReader | None = None,
) -> str:
    """Load an API key from environment override first, then macOS Keychain."""
    normalized = _normalize_provider(provider)
    env_name = _PROVIDER_ENV_VARS[normalized]
    env_map = env if env is not None else os.environ
    env_value = env_map.get(env_name)
    if isinstance(env_value, str) and env_value.strip():
        return _validated_secret(env_value)
    _require_keychain(platform)
    reader = native_reader or _load_with_security_framework
    try:
        return _validated_secret(reader(_service_name(normalized), env_name))
    except CredentialError:
        raise
    except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as exc:
        raise CredentialOperationError(
            f"Failed to read credential for provider '{normalized}'."
        ) from _redacted_native_exception(exc)


def store_api_key(
    provider: str,
    secret: str,
    *,
    platform: str | None = None,
    native_writer: NativeWriter | None = None,
) -> None:
    """Store an API key through Security.framework without exposing it in argv."""
    normalized = _normalize_provider(provider)
    normalized_secret = _validated_secret(secret)
    _require_keychain(platform)
    writer = native_writer or _store_with_security_framework
    try:
        writer(
            _service_name(normalized),
            _PROVIDER_ENV_VARS[normalized],
            normalized_secret,
        )
    except CredentialError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise CredentialOperationError(
            f"Failed to store credential for provider '{normalized}'."
        ) from _redacted_native_exception(exc)


def delete_api_key(
    provider: str,
    *,
    platform: str | None = None,
    native_deleter: NativeDeleter | None = None,
) -> None:
    """Delete a provider API key from macOS Keychain."""
    normalized = _normalize_provider(provider)
    _require_keychain(platform)
    deleter = native_deleter or _delete_with_security_framework
    try:
        deleter(_service_name(normalized), _PROVIDER_ENV_VARS[normalized])
    except CredentialError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise CredentialOperationError(
            f"Failed to delete credential for provider '{normalized}'."
        ) from _redacted_native_exception(exc)


def _normalize_provider(provider: str) -> str:
    if not isinstance(provider, str):
        raise ValueError("provider must be a string")
    normalized = provider.strip().lower()
    if normalized not in _PROVIDER_ENV_VARS:
        raise ValueError("provider must be one of: openai, anthropic, deepseek")
    return normalized


def _service_name(provider: str) -> str:
    return f"omd/{provider}/api-key"


class _SecurityBindings:
    def __init__(self) -> None:
        try:
            security = ctypes.CDLL(_SECURITY_FRAMEWORK)
            core_foundation = ctypes.CDLL(_CORE_FOUNDATION_FRAMEWORK)
            self.add_password = security.SecKeychainAddGenericPassword
            self.find_password = security.SecKeychainFindGenericPassword
            self.modify_item = security.SecKeychainItemModifyAttributesAndData
            self.delete_item = security.SecKeychainItemDelete
            self.free_content = security.SecKeychainItemFreeContent
            self.release = core_foundation.CFRelease
        except (AttributeError, OSError) as exc:
            raise CredentialCapabilityError(
                "macOS Keychain is unavailable because Security.framework could not be loaded."
            ) from _redacted_native_exception(exc)

        void_pointer = ctypes.c_void_p
        item_pointer = ctypes.POINTER(void_pointer)
        self.add_password.argtypes = [
            void_pointer,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            void_pointer,
            item_pointer,
        ]
        self.add_password.restype = ctypes.c_int32
        self.find_password.argtypes = [
            void_pointer,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            item_pointer,
            item_pointer,
        ]
        self.find_password.restype = ctypes.c_int32
        self.modify_item.argtypes = [
            void_pointer,
            void_pointer,
            ctypes.c_uint32,
            void_pointer,
        ]
        self.modify_item.restype = ctypes.c_int32
        self.delete_item.argtypes = [void_pointer]
        self.delete_item.restype = ctypes.c_int32
        self.free_content.argtypes = [void_pointer, void_pointer]
        self.free_content.restype = ctypes.c_int32
        self.release.argtypes = [void_pointer]
        self.release.restype = None

    def find(self, service: str, account: str, *, read_secret: bool) -> tuple[ctypes.c_void_p, bytes]:
        service_bytes = service.encode("utf-8")
        account_bytes = account.encode("utf-8")
        item = ctypes.c_void_p()
        password_length = ctypes.c_uint32()
        password_data = ctypes.c_void_p()
        status = self.find_password(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            ctypes.byref(password_length) if read_secret else None,
            ctypes.byref(password_data) if read_secret else None,
            ctypes.byref(item),
        )
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            raise CredentialNotFoundError("No stored credential exists in macOS Keychain.")
        if status != 0:
            raise CredentialOperationError(
                f"Failed to read credential from macOS Keychain (status {status})."
            )
        if not item.value:
            raise CredentialOperationError("macOS Keychain returned an invalid item reference.")
        secret = b""
        if read_secret:
            try:
                secret = ctypes.string_at(password_data, password_length.value)
            finally:
                if password_data.value:
                    self.free_content(None, password_data)
        return item, secret


def _security_bindings() -> _SecurityBindings:
    return _SecurityBindings()


def _load_with_security_framework(service: str, account: str) -> str:
    bindings = _security_bindings()
    item, secret = bindings.find(service, account, read_secret=True)
    try:
        return secret.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialOperationError("Stored macOS Keychain credential is not UTF-8 text.") from exc
    finally:
        bindings.release(item)


def _store_with_security_framework(service: str, account: str, secret: str) -> None:
    bindings = _security_bindings()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    secret_bytes = secret.encode("utf-8")
    secret_buffer = ctypes.create_string_buffer(secret_bytes)
    secret_pointer = ctypes.cast(secret_buffer, ctypes.c_void_p)
    try:
        status = bindings.add_password(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            len(secret_bytes),
            secret_pointer,
            None,
        )
        if status == _ERR_SEC_DUPLICATE_ITEM:
            item, _ignored = bindings.find(service, account, read_secret=False)
            try:
                status = bindings.modify_item(
                    item,
                    None,
                    len(secret_bytes),
                    secret_pointer,
                )
            finally:
                bindings.release(item)
        if status != 0:
            raise CredentialOperationError(
                f"Failed to store credential in macOS Keychain (status {status})."
            )
    finally:
        ctypes.memset(secret_pointer, 0, len(secret_bytes))


def _delete_with_security_framework(service: str, account: str) -> None:
    bindings = _security_bindings()
    item, _ignored = bindings.find(service, account, read_secret=False)
    try:
        status = bindings.delete_item(item)
    finally:
        bindings.release(item)
    if status != 0:
        raise CredentialOperationError(
            f"Failed to delete credential from macOS Keychain (status {status})."
        )


def _validated_secret(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("secret must be a non-empty string")
    normalized = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("secret must not contain control characters")
    return normalized


def _require_keychain(platform: str | None) -> None:
    current_platform = sys.platform if platform is None else platform
    if current_platform != "darwin":
        raise CredentialCapabilityError(
            "macOS Keychain is unavailable on this platform; no insecure fallback is allowed."
        )


def _redacted_native_exception(exc: BaseException) -> RuntimeError:
    return RuntimeError(type(exc).__name__)
