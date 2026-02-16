"""Authentication module for TCA."""

from .bootstrap_token import (
    BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
    BootstrapBearerTokenDependency,
    compute_token_sha256_digest,
    ensure_bootstrap_bearer_token,
    resolve_bootstrap_token_output_path,
)
from .service import request_login_code

__all__ = [
    "BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY",
    "BootstrapBearerTokenDependency",
    "compute_token_sha256_digest",
    "ensure_bootstrap_bearer_token",
    "request_login_code",
    "resolve_bootstrap_token_output_path",
]
