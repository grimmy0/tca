"""Authentication module for TCA."""

from .bootstrap_token import (
    BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
    BootstrapBearerTokenDependency,
    compute_token_sha256_digest,
    ensure_bootstrap_bearer_token,
    resolve_bootstrap_token_output_path,
)
from .encryption_utils import (
    AES_GCM_NONCE_BYTES,
    DATA_ENCRYPTION_KEY_BYTES,
    ENVELOPE_VERSION,
    EnvelopeDecryptionError,
    decrypt_with_envelope,
    encrypt_with_envelope,
    generate_data_encryption_key,
    unwrap_data_encryption_key,
    wrap_data_encryption_key,
)
from .kdf import (
    ARGON2ID_MEMORY_COST_KIB,
    ARGON2ID_PARALLELISM,
    ARGON2ID_SALT_BYTES,
    ARGON2ID_TIME_COST,
    KEY_ENCRYPTION_KEY_BYTES,
    derive_key_encryption_key,
)
from .service import request_login_code

__all__ = [
    "AES_GCM_NONCE_BYTES",
    "ARGON2ID_MEMORY_COST_KIB",
    "ARGON2ID_PARALLELISM",
    "ARGON2ID_SALT_BYTES",
    "ARGON2ID_TIME_COST",
    "BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY",
    "DATA_ENCRYPTION_KEY_BYTES",
    "ENVELOPE_VERSION",
    "KEY_ENCRYPTION_KEY_BYTES",
    "BootstrapBearerTokenDependency",
    "EnvelopeDecryptionError",
    "compute_token_sha256_digest",
    "decrypt_with_envelope",
    "derive_key_encryption_key",
    "encrypt_with_envelope",
    "ensure_bootstrap_bearer_token",
    "generate_data_encryption_key",
    "request_login_code",
    "resolve_bootstrap_token_output_path",
    "unwrap_data_encryption_key",
    "wrap_data_encryption_key",
]
