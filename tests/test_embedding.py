import importlib.util
import unittest

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from einstein.embedding import Embedder, SentenceTransformerEmbedder, TfidfEmbedder, embed_groups

QUANTUM_PAPER = "quantum error correction codes for fault tolerant computing"
QUANTUM_REPO = "quantum error correction simulator implementation"
COOKING_TEXT = "a recipe for baking sourdough bread at home"


class FakeWordCountEmbedder:
    """Minimal non-sklearn backend: raw word-count vectors over a fit-time
    vocabulary. Deterministic, collision-free, no ML dependency.

    Exists only to prove `embed_groups` works unchanged with any backend
    that satisfies the `Embedder` interface -- the acceptance bar for
    einstein-10 ("swapping backend changes no caller code").
    """

    def __init__(self) -> None:
        self.fit_calls = 0
        self._vocab: dict[str, int] = {}

    def fit(self, texts: list[str]) -> "FakeWordCountEmbedder":
        self.fit_calls += 1
        self._vocab = {}
        for text in texts:
            for word in text.lower().split():
                self._vocab.setdefault(word, len(self._vocab))
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, len(self._vocab)))
        rows = []
        for text in texts:
            vec = np.zeros(len(self._vocab))
            for word in text.lower().split():
                idx = self._vocab.get(word)
                if idx is not None:
                    vec[idx] += 1.0
            rows.append(vec)
        return np.array(rows)

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        self.fit(texts)
        return self.transform(texts)


class TfidfEmbedderTest(unittest.TestCase):
    def test_fit_transform_shape(self):
        embedder = TfidfEmbedder()
        matrix = embedder.fit_transform([QUANTUM_PAPER, QUANTUM_REPO, COOKING_TEXT])
        self.assertEqual(matrix.shape[0], 3)
        self.assertGreater(matrix.shape[1], 0)

    def test_separate_fit_then_transform_matches_fit_transform(self):
        texts = [QUANTUM_PAPER, QUANTUM_REPO, COOKING_TEXT]
        one_shot = TfidfEmbedder().fit_transform(texts)

        two_step = TfidfEmbedder()
        two_step.fit(texts)
        result = two_step.transform(texts)

        np.testing.assert_allclose(one_shot, result)

    def test_transform_before_fit_raises(self):
        embedder = TfidfEmbedder()
        with self.assertRaises(AssertionError):
            embedder.transform([QUANTUM_PAPER])

    def test_same_topic_more_similar_than_different_topic(self):
        embedder = TfidfEmbedder()
        matrix = embedder.fit_transform([QUANTUM_PAPER, QUANTUM_REPO, COOKING_TEXT])
        sim = cosine_similarity(matrix)
        # paper<->repo (same topic) should beat paper<->cooking (unrelated)
        self.assertGreater(sim[0, 1], sim[0, 2])

    def test_satisfies_embedder_protocol(self):
        self.assertIsInstance(TfidfEmbedder(), Embedder)


class EmbedGroupsTest(unittest.TestCase):
    def test_default_backend_is_tfidf(self):
        groups = embed_groups(
            {"papers": [QUANTUM_PAPER], "repos": [QUANTUM_REPO], "patents": [COOKING_TEXT]}
        )
        self.assertEqual(set(groups), {"papers", "repos", "patents"})
        # shared vector space: every group embedded into the same width
        widths = {matrix.shape[1] for matrix in groups.values()}
        self.assertEqual(len(widths), 1)

    def test_order_preserved_within_group(self):
        groups = embed_groups({"papers": [QUANTUM_PAPER, COOKING_TEXT], "repos": [QUANTUM_REPO]})
        self.assertEqual(groups["papers"].shape[0], 2)
        self.assertEqual(groups["repos"].shape[0], 1)

    def test_empty_group_yields_empty_matrix(self):
        groups = embed_groups({"papers": [QUANTUM_PAPER], "repos": []})
        self.assertEqual(groups["repos"].shape[0], 0)

    def test_fits_once_over_union_of_all_groups(self):
        fake = FakeWordCountEmbedder()
        embed_groups({"papers": [QUANTUM_PAPER], "repos": [QUANTUM_REPO]}, embedder=fake)
        self.assertEqual(fake.fit_calls, 1)

    def test_backend_swap_changes_no_caller_code(self):
        """The einstein-10 acceptance bar: same call, different embedder in."""
        groups_input = {"papers": [QUANTUM_PAPER, COOKING_TEXT], "repos": [QUANTUM_REPO]}

        tfidf_result = embed_groups(groups_input, embedder=TfidfEmbedder())
        fake_result = embed_groups(groups_input, embedder=FakeWordCountEmbedder())

        for result in (tfidf_result, fake_result):
            self.assertEqual(set(result), {"papers", "repos"})
            self.assertEqual(result["papers"].shape[0], 2)
            self.assertEqual(result["repos"].shape[0], 1)
            widths = {matrix.shape[1] for matrix in result.values()}
            self.assertEqual(len(widths), 1)

    def test_cross_group_similarity_survives_backend_swap(self):
        """Same-topic paper/repo pair should score higher than the unrelated
        one under both backends -- not just matching shapes."""
        groups_input = {"papers": [QUANTUM_PAPER, COOKING_TEXT], "repos": [QUANTUM_REPO]}

        for embedder in (TfidfEmbedder(), FakeWordCountEmbedder()):
            result = embed_groups(groups_input, embedder=embedder)
            sim = cosine_similarity(result["papers"], result["repos"])
            self.assertGreater(sim[0, 0], sim[1, 0])


@unittest.skipIf(
    importlib.util.find_spec("sentence_transformers") is not None,
    "sentence-transformers is installed; import-guard path not exercised",
)
class SentenceTransformerEmbedderTest(unittest.TestCase):
    def test_missing_dependency_raises_helpful_import_error(self):
        with self.assertRaises(ImportError):
            SentenceTransformerEmbedder()


if __name__ == "__main__":
    unittest.main()
