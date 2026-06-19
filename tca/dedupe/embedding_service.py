"""Embedding service for semantic similarity deduplication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from tca.normalize import build_similarity_normalized_input

if TYPE_CHECKING:
    from numpy.typing import NDArray
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIMENSIONS = 384
EMBEDDING_DTYPE = np.float32

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Return a lazily-loaded singleton SentenceTransformer model."""
    global _model  # noqa: PLW0603
    if _model is None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def compute_embedding(*, text: str) -> bytes:
    """Encode text and return the L2-normalized embedding as float32 bytes."""
    model = get_model()
    vector: NDArray[np.float32] = model.encode(  # pyright: ignore[reportUnknownMemberType]
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vector.astype(EMBEDDING_DTYPE).tobytes()


def embedding_to_array(blob: bytes) -> NDArray[np.float32]:
    """Deserialize an embedding BLOB to a writable numpy array."""
    return np.frombuffer(blob, dtype=EMBEDDING_DTYPE).copy()


def prepare_text_for_embedding(*, title: str | None, body: str | None) -> str:
    """Build normalized text with the E5 query prefix for embedding."""
    normalized = build_similarity_normalized_input(title=title, body=body)
    return f"query: {normalized}"


def reset_model() -> None:
    """Reset the singleton model instance (test utility)."""
    global _model  # noqa: PLW0603
    _model = None
