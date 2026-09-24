"""Evaluation metric behaviour."""

from __future__ import annotations

from app.evaluation import contains_all, score_answer, score_retrieval


class TestContainsAll:
    def test_thousands_separators_are_ignored(self):
        assert contains_all("Total net sales were 82,959 million", ["82959"])
        assert contains_all("Total net sales were 82959 million", ["82,959"])

    def test_matching_is_case_insensitive(self):
        assert contains_all("The AMERICAS segment led", ["americas"])

    def test_every_expected_value_must_appear(self):
        assert not contains_all("Only 82,959 here", ["82,959", "282,457"])

    def test_no_expectation_is_vacuously_satisfied(self):
        assert contains_all("anything", [])


class TestScoreRetrieval:
    def test_hit_recall_and_rank(self):
        score = score_retrieval([9, 4, 18], [4, 18])
        assert score.hit
        assert score.recall == 1.0
        assert score.first_correct_rank == 2
        assert score.reciprocal_rank == 0.5

    def test_complete_miss(self):
        score = score_retrieval([9, 10], [4])
        assert not score.hit
        assert score.recall == 0.0
        assert score.reciprocal_rank == 0.0

    def test_partial_recall(self):
        score = score_retrieval([4], [4, 18])
        assert score.hit
        assert score.recall == 0.5

    def test_unsupported_questions_are_excluded_rather_than_scored_zero(self):
        """There is no correct page, so a zero would punish correct behaviour."""
        score = score_retrieval([1, 2, 3], [])
        assert score.hit
        assert score.recall == 1.0


class TestScoreAnswer:
    def test_correct_value_and_citation(self):
        score = score_answer("Total net sales were 82,959 million.", False, [4], [4], ["82,959"], False)
        assert score.correct
        assert score.citation_correct
        assert not score.hallucinated

    def test_wrong_value_is_incorrect(self):
        score = score_answer("Total net sales were 81,434 million.", False, [4], [4], ["82,959"], False)
        assert not score.correct

    def test_correct_refusal(self):
        score = score_answer("The filing does not cover 2024.", True, [], [], [], True)
        assert score.correct
        assert score.refusal_correct
        assert not score.hallucinated

    def test_answering_an_unsupported_question_is_a_hallucination(self):
        score = score_answer("Apple's 2024 net sales were $391 billion.", False, [4], [], [], True)
        assert score.hallucinated
        assert not score.refusal_correct
        assert not score.correct

    def test_refusing_a_supported_question_is_incorrect(self):
        score = score_answer("Not found.", True, [], [4], ["82,959"], False)
        assert not score.correct
        assert not score.refusal_correct

    def test_citing_the_wrong_page_is_detected(self):
        score = score_answer("Total net sales were 82,959 million.", False, [9], [4], ["82,959"], False)
        assert score.correct
        assert not score.citation_correct
