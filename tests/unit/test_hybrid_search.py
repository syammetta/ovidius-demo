"""Tests for hybrid search and RRF fusion.

Key bugs to catch:
- RRF with empty result lists
- Duplicate chunks across vector and BM25 results
- Score calculation correctness
- Ranking order preservation
"""

import pytest
from app.retrieval.hybrid_search import reciprocal_rank_fusion
from tests.conftest import make_chunk


class TestReciprocalRankFusion:
    def test_basic_fusion(self):
        vector = [make_chunk(chunk_id="a", score=0.9), make_chunk(chunk_id="b", score=0.8)]
        bm25 = [make_chunk(chunk_id="b", score=5.0), make_chunk(chunk_id="c", score=3.0)]

        fused = reciprocal_rank_fusion(vector, bm25, top_n=10)
        ids = [c.chunk_id for c in fused]

        assert "b" in ids, "Chunk appearing in both lists should be in results"
        assert len(fused) == 3, "Should have 3 unique chunks"

    def test_duplicate_chunk_gets_higher_score(self):
        vector = [make_chunk(chunk_id="a"), make_chunk(chunk_id="shared")]
        bm25 = [make_chunk(chunk_id="shared"), make_chunk(chunk_id="b")]

        fused = reciprocal_rank_fusion(vector, bm25, top_n=10)
        scores = {c.chunk_id: c.score for c in fused}

        assert scores["shared"] > scores["a"], "Chunk in both lists should rank higher"
        assert scores["shared"] > scores["b"]

    def test_empty_vector_results(self):
        bm25 = [make_chunk(chunk_id="a"), make_chunk(chunk_id="b")]
        fused = reciprocal_rank_fusion([], bm25, top_n=10)
        assert len(fused) == 2

    def test_empty_bm25_results(self):
        vector = [make_chunk(chunk_id="a"), make_chunk(chunk_id="b")]
        fused = reciprocal_rank_fusion(vector, [], top_n=10)
        assert len(fused) == 2

    def test_both_empty(self):
        fused = reciprocal_rank_fusion([], [], top_n=10)
        assert len(fused) == 0

    def test_top_n_respected(self):
        vector = [make_chunk(chunk_id=f"v{i}") for i in range(10)]
        bm25 = [make_chunk(chunk_id=f"b{i}") for i in range(10)]
        fused = reciprocal_rank_fusion(vector, bm25, top_n=5)
        assert len(fused) == 5

    def test_rrf_scores_are_positive(self):
        vector = [make_chunk(chunk_id="a")]
        fused = reciprocal_rank_fusion(vector, top_n=10)
        for chunk in fused:
            assert chunk.score > 0

    def test_rank_order_matters(self):
        """First item in a list should contribute more than last."""
        list1 = [make_chunk(chunk_id="first"), make_chunk(chunk_id="second")]
        fused = reciprocal_rank_fusion(list1, top_n=10)
        scores = {c.chunk_id: c.score for c in fused}
        assert scores["first"] > scores["second"]

    def test_retrieval_method_set_to_hybrid(self):
        vector = [make_chunk(chunk_id="a")]
        fused = reciprocal_rank_fusion(vector, top_n=10)
        assert fused[0].retrieval_method == "hybrid_rrf"

    def test_three_lists(self):
        """RRF should work with any number of result lists."""
        l1 = [make_chunk(chunk_id="a")]
        l2 = [make_chunk(chunk_id="a"), make_chunk(chunk_id="b")]
        l3 = [make_chunk(chunk_id="c"), make_chunk(chunk_id="a")]

        fused = reciprocal_rank_fusion(l1, l2, l3, top_n=10)
        ids = [c.chunk_id for c in fused]
        assert ids[0] == "a", "Chunk in all 3 lists should be ranked first"
