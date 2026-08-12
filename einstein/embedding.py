"""Pluggable text-embedding backends behind one interface.

Every backend maps ``list[str]`` -> ``np.ndarray`` of shape
``(n_texts, n_dims)``. Gap detection fits one embedder over the union of
paper/repo/patent summaries -- a single shared vector space -- then
transforms each source's texts separately so the resulting matrices are
directly comparable with cosine similarity.

Swapping the default TF-IDF backend for a dense one (e.g.
``SentenceTransformerEmbedder``) must not require any change to caller code:
both implement the same ``fit`` / ``transform`` / ``fit_transform`` surface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Embedder(Protocol):
    """Interface every embedding backend implements."""

    def fit(self, texts: list[str]) -> "Embedder":
        """Fit the shared vector space over `texts`. Returns self."""
        ...

    def transform(self, texts: list[str]) -> np.ndarray:
        """Embed `texts` into the space established by `fit`."""
        ...

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        """Fit on `texts` and embed them in one step."""
        ...


class TfidfEmbedder:
    """Default backend: scikit-learn TF-IDF over a shared vocabulary."""

    def __init__(self, max_features: int = 1000) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(stop_words="english", max_features=max_features)
        self._fitted = False

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        self._vectorizer.fit(texts)
        self._fitted = True
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        assert self._fitted, "TfidfEmbedder.transform called before fit"
        if not texts:
            return np.zeros((0, len(self._vectorizer.vocabulary_)))
        return self._vectorizer.transform(texts).toarray()

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        matrix = self._vectorizer.fit_transform(texts).toarray()
        self._fitted = True
        return matrix


class SentenceTransformerEmbedder:
    """Drop-in dense backend: sentence-transformers/all-MiniLM-L6-v2.

    Not a pinned project dependency -- it pulls in torch and downloads model
    weights on first use, which tests must not do. Importing this class is
    always safe; instantiating it requires `pip install sentence-transformers`.
    Fitting is a no-op (the model is pretrained), kept only so this class
    satisfies the same `Embedder` interface as `TfidfEmbedder`.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "SentenceTransformerEmbedder requires the optional "
                "'sentence-transformers' package. Install it with: "
                "uv add sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(model_name)

    def fit(self, texts: list[str]) -> "SentenceTransformerEmbedder":
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts))

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        return self.transform(texts)


def embed_groups(
    groups: dict[str, list[str]], embedder: Embedder | None = None
) -> dict[str, np.ndarray]:
    """Embed several named text groups into one shared vector space.

    Fits `embedder` (default: `TfidfEmbedder()`) over the concatenation of
    every group's texts, then transforms each group separately so rows
    across groups live in the same space and can be compared with cosine
    similarity (e.g. papers x repos, papers x patents in einstein-11).

    Order within a group is preserved: `result[name][i]` is the embedding of
    `groups[name][i]`. Empty groups map to an array with shape (0, n_dims).
    """
    if embedder is None:
        embedder = TfidfEmbedder()

    all_texts = [text for texts in groups.values() for text in texts]
    embedder.fit(all_texts)

    return {name: embedder.transform(texts) for name, texts in groups.items()}
