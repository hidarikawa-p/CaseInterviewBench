"""Score computation.

Four scores on a 0-100 scale:

* problem_recognition: mean of judged problem_formulation, problem_evidence
  (each /3), and the fraction of required_for_problem_formulation facts acquired.
* resolution: mean of judged resolution, resolution_rationale (each /3), and the
  fraction of required_for_resolution facts acquired.
* efficiency: mean of eff_a and eff_b.
    - eff_a (case A) = coverage of required facts * restraint after acquiring them,
      where restraint = 1 - (questions_used - k) / (max_questions - k) and k is the
      question index at which the last required fact was acquired. Zero if no
      required fact was acquired; restraint is 1 when k == max_questions.
    - eff_b = disclosed facts / questions used (capped at 1).
* overall: mean of the three scores.

Protocol violations are NOT folded into any score; they cost a consumed
question only. They are reported separately for aggregation.
"""

from __future__ import annotations


def _ratio(subset: set[int], acquired: set[int]) -> float:
    return 1.0 if not subset else len(subset & acquired) / len(subset)


def compute_scores(
    judge_result: dict,
    disclosed_ids: set[int],
    first_disclosure: dict[int, int],
    required_pf: set[int],
    required_res: set[int],
    n_hidden_facts: int,
    question_count: int,
    max_questions: int,
) -> dict:
    """Return a dict of scores and their components."""
    s = judge_result["scores"]
    pf = s["problem_formulation"]["score"] / 3
    pe = s["problem_evidence"]["score"] / 3
    rs = s["resolution"]["score"] / 3
    rr = s["resolution_rationale"]["score"] / 3

    pf_fact_ratio = _ratio(required_pf, disclosed_ids)
    res_fact_ratio = _ratio(required_res, disclosed_ids)

    problem_score = (pf + pe + pf_fact_ratio) / 3 * 100
    resolution_score = (rs + rr + res_fact_ratio) / 3 * 100

    required_all = required_pf | required_res
    coverage = _ratio(required_all, disclosed_ids)
    required_hits = {fid: q for fid, q in first_disclosure.items() if fid in required_all}

    if not required_hits:
        k = None
        restraint = 0.0
        eff_a = 0.0
    else:
        k = max(required_hits.values())
        if k >= max_questions:
            restraint = 1.0
        else:
            restraint = max(0.0, 1.0 - (question_count - k) / (max_questions - k))
        eff_a = coverage * restraint

    eff_b = min(1.0, len(disclosed_ids) / question_count) if question_count else 0.0
    efficiency_score = (eff_a + eff_b) / 2 * 100
    overall = (problem_score + resolution_score + efficiency_score) / 3

    return {
        "scores": {
            "problem_recognition": round(problem_score, 1),
            "resolution": round(resolution_score, 1),
            "efficiency": round(efficiency_score, 1),
            "overall": round(overall, 1),
        },
        "components": {
            "pf_judge": s["problem_formulation"]["score"],
            "pe_judge": s["problem_evidence"]["score"],
            "rs_judge": s["resolution"]["score"],
            "rr_judge": s["resolution_rationale"]["score"],
            "pf_fact_ratio": round(pf_fact_ratio, 3),
            "res_fact_ratio": round(res_fact_ratio, 3),
            "coverage": round(coverage, 3),
            "k": k,
            "restraint": round(restraint, 3),
            "eff_a": round(eff_a, 3),
            "eff_b": round(eff_b, 3),
        },
    }
