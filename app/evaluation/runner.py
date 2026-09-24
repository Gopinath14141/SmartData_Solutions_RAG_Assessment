"""Run the evaluation set and report measured results.

**On not using an LLM judge.** Plan §15 permits one and asks that its
limitations be documented. This evaluation uses deterministic checks instead,
for three reasons specific to this task:

* Most questions have a single correct numeric answer read directly from the
  filing. A substring check on a normalised number is exact; a judge would add
  its own error rate to a measurement that does not need one.
* The behaviour most worth measuring is refusal on unsupported questions, and
  that is a boolean the system already reports.
* A judge sharing a provider with the system under test is not independent.

The limitation this leaves is real and is reported rather than glossed: for
narrative questions with no expected string, correctness is **not** measured.
Those questions are scored on retrieval and refusal only, and the summary says
so. Fluency, completeness, and groundedness of prose answers are not assessed
automatically.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.evaluation.metrics import AnswerScore, RetrievalScore, score_answer, score_retrieval
from app.generation import Answerer

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "tests" / "evaluation_questions.json"


@dataclass
class QuestionResult:
    """Everything measured for one question."""

    id: str
    category: str
    question: str
    answer: str
    refused: bool
    should_refuse: bool
    expected_pages: list[int]
    retrieved_pages: list[int]
    cited_pages: list[int]
    retrieval: RetrievalScore
    answer_score: AnswerScore
    graded_for_correctness: bool
    seconds: float
    used_vision: bool

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["retrieval"] = asdict(self.retrieval)
        data["answer_score"] = asdict(self.answer_score)
        return data


@dataclass
class EvaluationResults:
    """Aggregate results across the whole set."""

    results: list[QuestionResult] = field(default_factory=list)
    top_k: int = 0
    llm_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""
    started_at: str = ""
    seconds_total: float = 0.0
    retrieval_config: dict[str, float] = field(default_factory=dict)
    """Full retrieval configuration.

    Recorded because a run is only comparable to another if the candidate
    windows and fusion constants match. An earlier comparison nearly went wrong
    for want of this: two runs that differed only in ``dense_top_k`` were
    indistinguishable in the saved output.
    """

    # ── aggregates ────────────────────────────────────────────────────────

    def _subset(self, category: str | None = None) -> list[QuestionResult]:
        return [r for r in self.results if category is None or r.category == category]

    @property
    def hit_rate(self) -> float:
        scored = [r for r in self.results if r.expected_pages]
        return statistics.fmean(r.retrieval.hit for r in scored) if scored else 0.0

    @property
    def recall_at_k(self) -> float:
        scored = [r for r in self.results if r.expected_pages]
        return statistics.fmean(r.retrieval.recall for r in scored) if scored else 0.0

    @property
    def precision_at_k(self) -> float:
        scored = [r for r in self.results if r.expected_pages]
        return statistics.fmean(r.retrieval.precision for r in scored) if scored else 0.0

    @property
    def mrr(self) -> float:
        scored = [r for r in self.results if r.expected_pages]
        return statistics.fmean(r.retrieval.reciprocal_rank for r in scored) if scored else 0.0

    @property
    def answer_accuracy(self) -> float:
        graded = [r for r in self.results if r.graded_for_correctness]
        return statistics.fmean(r.answer_score.correct for r in graded) if graded else 0.0

    @property
    def refusal_accuracy(self) -> float:
        return statistics.fmean(r.answer_score.refusal_correct for r in self.results) if self.results else 0.0

    @property
    def hallucination_rate(self) -> float:
        negatives = [r for r in self.results if r.should_refuse]
        return statistics.fmean(r.answer_score.hallucinated for r in negatives) if negatives else 0.0

    @property
    def false_refusal_rate(self) -> float:
        positives = [r for r in self.results if not r.should_refuse]
        return statistics.fmean(r.refused for r in positives) if positives else 0.0

    @property
    def citation_accuracy(self) -> float:
        scored = [r for r in self.results if r.expected_pages and not r.should_refuse]
        return statistics.fmean(r.answer_score.citation_correct for r in scored) if scored else 0.0

    @property
    def median_seconds(self) -> float:
        return statistics.median(r.seconds for r in self.results) if self.results else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "models": {
                "llm": self.llm_model,
                "vision": self.vision_model,
                "embedding": self.embedding_model,
            },
            "top_k": self.top_k,
            "retrieval_config": self.retrieval_config,
            "questions": len(self.results),
            "graded_for_correctness": sum(1 for r in self.results if r.graded_for_correctness),
            "retrieval": {
                "hit_rate": round(self.hit_rate, 4),
                "recall_at_k": round(self.recall_at_k, 4),
                "precision_at_k": round(self.precision_at_k, 4),
                "mrr": round(self.mrr, 4),
            },
            "answers": {
                "accuracy_on_graded": round(self.answer_accuracy, 4),
                "refusal_accuracy": round(self.refusal_accuracy, 4),
                "hallucination_rate": round(self.hallucination_rate, 4),
                "false_refusal_rate": round(self.false_refusal_rate, 4),
                "citation_accuracy": round(self.citation_accuracy, 4),
            },
            "latency": {
                "median_seconds": round(self.median_seconds, 3),
                "total_seconds": round(self.seconds_total, 2),
            },
            "results": [r.as_dict() for r in self.results],
        }

    def render(self) -> str:
        """A plain-text summary, honest about what was and was not measured."""
        graded = sum(1 for r in self.results if r.graded_for_correctness)
        ungraded = len(self.results) - graded

        lines = [
            "",
            f"Evaluation — {len(self.results)} questions, top_k={self.top_k}",
            f"  models: {self.llm_model} / vision {self.vision_model} / {self.embedding_model}",
            "",
            "Retrieval (page-level, over questions with known answer pages)",
            f"  Hit rate@k     {self.hit_rate:6.1%}   at least one correct page retrieved",
            f"  Recall@k       {self.recall_at_k:6.1%}   fraction of correct pages retrieved",
            f"  Precision@k    {self.precision_at_k:6.1%}   fraction of retrieved chunks on a correct page",
            f"  MRR            {self.mrr:6.3f}   1/rank of the first correct chunk",
            "",
            "Answers",
            f"  Accuracy       {self.answer_accuracy:6.1%}   over {graded} questions with a checkable value",
            f"  Refusal acc.   {self.refusal_accuracy:6.1%}   declined exactly when it should have",
            f"  Hallucination  {self.hallucination_rate:6.1%}   answered an unsupported question",
            f"  False refusal  {self.false_refusal_rate:6.1%}   declined a question the filing does cover",
            f"  Citation acc.  {self.citation_accuracy:6.1%}   cited a page that actually holds the answer",
            "",
            "Latency",
            f"  Median {self.median_seconds:.2f}s per question, {self.seconds_total:.1f}s total",
            "",
            "By category",
        ]

        for category in ("table", "text", "figure", "unsupported"):
            subset = self._subset(category)
            if not subset:
                continue
            subset_graded = [r for r in subset if r.graded_for_correctness]
            accuracy = (
                f"{statistics.fmean(r.answer_score.correct for r in subset_graded):6.1%}"
                if subset_graded
                else "     —"
            )
            hits = [r for r in subset if r.expected_pages]
            hit = f"{statistics.fmean(r.retrieval.hit for r in hits):6.1%}" if hits else "     —"
            lines.append(
                f"  {category:<12} n={len(subset):<3} hit@k {hit}   accuracy {accuracy}"
                f"   ({len(subset_graded)} graded)"
            )

        if ungraded:
            lines += [
                "",
                f"Not measured: correctness of {ungraded} narrative answers with no single checkable",
                "value. Those are scored on retrieval and refusal only. No LLM judge was used;",
                "see app/evaluation/runner.py for why, and for what that leaves unmeasured.",
            ]

        failures = [r for r in self.results if not r.answer_score.correct or not r.answer_score.refusal_correct]
        if failures:
            lines += ["", "Failures"]
            for result in failures:
                reason = "hallucinated" if result.answer_score.hallucinated else (
                    "refused a supported question" if result.refused else "wrong or missing value"
                )
                lines.append(f"  {result.id:<8} {reason:<28} {result.question[:58]}")

        return "\n".join(lines)


def run_evaluation(
    questions_path: str | Path | None = None,
    output_path: str | Path | None = None,
    settings: Settings | None = None,
    answerer: Answerer | None = None,
) -> EvaluationResults:
    """Run every question and collect measurements."""
    settings = settings or get_settings()
    answerer = answerer or Answerer(settings=settings)

    path = Path(questions_path) if questions_path else DEFAULT_QUESTIONS
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload["questions"]

    results = EvaluationResults(
        top_k=settings.retrieval_top_k,
        llm_model=settings.llm_model,
        vision_model=settings.vision_model,
        embedding_model=settings.embedding_model,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        retrieval_config={
            "retrieval_top_k": settings.retrieval_top_k,
            "dense_top_k": settings.dense_top_k,
            "bm25_top_k": settings.bm25_top_k,
            "rrf_k": settings.rrf_k,
            "router_boost": settings.router_boost,
            "text_chunk_size": settings.text_chunk_size,
            "text_chunk_overlap": settings.text_chunk_overlap,
        },
    )

    started = time.perf_counter()

    for item in questions:
        logger.info("evaluating %s", item["id"])
        question_started = time.perf_counter()
        answer = answerer.answer(item["question"])
        elapsed = time.perf_counter() - question_started

        retrieved_pages = [e.pdf_page for e in answer.evidence]
        cited_pages = [e.pdf_page for e in answer.evidence if e.cited_by_model]
        expected_pages = item.get("expected_pages", [])
        expected_contains = item.get("answer_contains", [])
        should_refuse = bool(item.get("should_refuse", False))

        results.results.append(
            QuestionResult(
                id=item["id"],
                category=item["category"],
                question=item["question"],
                answer=answer.text,
                refused=answer.refused,
                should_refuse=should_refuse,
                expected_pages=expected_pages,
                retrieved_pages=retrieved_pages,
                cited_pages=cited_pages,
                retrieval=score_retrieval(retrieved_pages, expected_pages),
                answer_score=score_answer(
                    answer.text,
                    answer.refused,
                    cited_pages,
                    expected_pages,
                    expected_contains,
                    should_refuse,
                ),
                # Narrative questions carry no checkable value, so their
                # correctness is excluded from the accuracy average rather than
                # counted as a free pass.
                graded_for_correctness=bool(expected_contains) or should_refuse,
                seconds=elapsed,
                used_vision=answer.used_vision,
            )
        )

    results.seconds_total = time.perf_counter() - started

    destination = (
        Path(output_path)
        if output_path
        else settings.eval_runs_dir / f"eval_{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results.as_dict(), indent=2), encoding="utf-8")
    logger.info("evaluation written to %s", destination)

    return results
