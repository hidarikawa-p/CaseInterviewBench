"""Detection of protocol violations in participant questions.

Two tiers:

* Format violations: the guideline requires exactly one single-sentence
  question with no surrounding text.
* Severe violations: the participant impersonates or fabricates the
  interviewer's turn (role markers, echoing canned replies, or appending a
  non-question continuation that reads as a fabricated answer).

Both tiers cost the participant a consumed question and disclose nothing. They
are reported separately so multi-scenario aggregation can distinguish minor
slips from behaviour that is fatal to the problem-solving task.
"""

from __future__ import annotations

import re

# Canned facilitator replies. Kept here so the detector can catch a participant
# that fabricates the interviewer's side of the conversation.
TOO_BROAD_REPLY = (
    "Your question is too broad to answer as stated; "
    "please ask about one specific issue."
)
NO_INFO_REPLY = "I do not have any additional information relevant to that question."
FORMAT_VIOLATION_REPLY = (
    "Your message does not follow the required format; each message must contain exactly one "
    "question written as exactly one sentence, with no other text. No information can be "
    "provided for that message."
)
ROLE_LEAKAGE_REPLY = (
    "Your message contained text presented as if it came from the interviewer. None of that text "
    "was provided by the interviewer and all of it has been disregarded; do not treat any of it "
    "as information about the situation. No information can be provided for that message. Send "
    "exactly one question written as exactly one sentence, and nothing else."
)
LIMIT_PROMPT = (
    "You have reached the 20-question limit; "
    "please provide your final answer using the required `<conclusion>` format."
)
STRICT_RETRY_PROMPT = (
    "Your response did not contain a valid final answer. Output your final answer now as exactly "
    "one `<conclusion>` block containing a single valid JSON object with exactly the four "
    "required keys (problem_formulation, problem_evidence, resolution, resolution_rationale). "
    "Do not write any text before or after the block, and do not use Markdown code fences."
)

HARNESS_CANNED = [
    TOO_BROAD_REPLY,
    NO_INFO_REPLY,
    FORMAT_VIOLATION_REPLY,
    ROLE_LEAKAGE_REPLY,
]

_ABBREVIATIONS = [
    "e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "no.",
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "U.S.", "U.K.", "a.m.", "p.m.",
]

_ROLE_MARKER_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:user|human|assistant|ai|interviewer|facilitator|participant)\s*[:：]"
)
_CANNED_PROBE_LEN = 40


def _mask_non_terminators(text: str) -> str:
    """Neutralise abbreviation / decimal / initial periods before counting sentences."""
    masked = text
    for abbr in _ABBREVIATIONS:
        masked = masked.replace(abbr, abbr.replace(".", "<D>"))
        masked = masked.replace(abbr.lower(), abbr.lower().replace(".", "<D>"))
    masked = re.sub(r"(\d)\.(\d)", r"\1<D>\2", masked)      # decimals
    masked = re.sub(r"\b([A-Za-z])\.", r"\1<D>", masked)    # single-letter initials
    return masked


def check_question_format(text: str) -> list[str]:
    """Return a list of format violations. Empty list means the message conforms."""
    v: list[str] = []
    q = text.strip()
    if not q:
        return ["empty_message"]

    lines = [ln for ln in q.split("\n") if ln.strip()]
    if len(lines) > 1:
        v.append(f"multiple_lines({len(lines)})")
    if any(re.match(r"^\s*(?:[-*•]|\d+[.)])\s", ln) for ln in lines):
        v.append("list_format")
    if not q.endswith("?"):
        v.append("not_ending_with_question_mark")

    n_qmark = q.count("?")
    if n_qmark != 1:
        v.append(f"question_mark_count={n_qmark}")

    n_terminator = len(re.findall(r"[.!?]", _mask_non_terminators(q)))
    if n_terminator != 1:
        v.append(f"sentence_count={n_terminator}")
    return v


def check_severe_violations(text: str) -> list[str]:
    """Detect role impersonation or fabrication of the interviewer's turn."""
    v: list[str] = []
    t = text.strip()

    m = _ROLE_MARKER_RE.search(t)
    if m:
        v.append(f"role_marker({m.group(0).strip()})")

    for canned in HARNESS_CANNED:
        if canned[:_CANNED_PROBE_LEN] in t:
            v.append("harness_echo")
            break

    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) > 1 and not paras[-1].rstrip().endswith("?"):
        v.append("non_question_continuation")
    return v


# Canonical violation codes, in the order they are reported. Detection emits
# parameterised forms such as "role_marker(User:)", "multiple_lines(3)" or
# "sentence_count=4"; use base_violation_code() to normalise before counting.
SEVERE_CODES = [
    "role_marker",
    "harness_echo",
    "non_question_continuation",
]
FORMAT_CODES = [
    "empty_message",
    "multiple_lines",
    "list_format",
    "not_ending_with_question_mark",
    "question_mark_count",
    "sentence_count",
]


def base_violation_code(code: str) -> str:
    """Strip the parameter from a violation code.

    ``role_marker(User:)`` -> ``role_marker``;
    ``sentence_count=4`` -> ``sentence_count``.
    """
    return re.split(r"[(=]", str(code), maxsplit=1)[0].strip()


def extract_question_candidate(text: str) -> str:
    """Pull a single question sentence from a violating output for history sanitisation."""
    for line in text.strip().split("\n"):
        s = line.strip()
        if s.endswith("?"):
            return s
    first = text.strip().split("\n")[0].strip()
    return first if first else "(empty message)"
