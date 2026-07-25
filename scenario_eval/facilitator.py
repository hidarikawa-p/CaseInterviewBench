"""Facilitator: decides which single hidden fact (if any) to disclose.

The facilitator sees only the public context, the undisclosed facts, and the
current question - never the conversation history. It judges each question on
its own and discloses at most one fact per question.
"""

from __future__ import annotations

from typing import Any

from .config import extract_json
from .providers import BaseAdapter
from .violations import NO_INFO_REPLY, TOO_BROAD_REPLY

FACILITATOR_SYSTEM = """You are the facilitator in an interactive problem-solving evaluation.

A participant is investigating a scenario by asking one question at a time. For the single
question given to you, decide which of the remaining undisclosed hidden facts (if any) must be
disclosed. You do not see the conversation history: judge the question on its own.

## Public Context

{public_context}

## Remaining Undisclosed Hidden Facts

{facts_block}

## Participant's Question

{question}

## Decision Procedure

1. First decide whether the question is too broad. A question is too broad when it does not
   target one specific issue: for example it asks for everything relevant at once, bundles
   several independent matters, or is an open request such as "is there anything else I should
   know?". If the question is too broad, set "too_broad" to true, leave "qualifying_fact_ids"
   empty and "best_fact_id" null, even if some fact would otherwise match its reveal condition.
2. Otherwise, evaluate each undisclosed fact independently. A fact qualifies only when the
   question satisfies its "reveal_if" condition AND does not fall under its "do_not_reveal_if"
   condition. List every qualifying fact in "qualifying_fact_ids".
3. At most one fact may be disclosed per question. Set "best_fact_id" to the single most
   appropriate qualifying fact, chosen by these rules in order:
   a. the fact whose "reveal_if" condition most directly matches what the question actually
      asks about, rather than one that merely touches an adjacent topic;
   b. if two or more are equally direct, the one with the smallest fact_id.
   If no fact qualifies, set "best_fact_id" to null.
4. Facts that qualify but are not selected remain available for later questions. Never mention,
   hint at, allude to, or summarise them.
5. Judge by the substance of what is asked, not by exact wording.
6. Never invent information and never select a fact whose conditions are not met.

## Output

Return exactly one JSON object and nothing else:

{{"too_broad": true or false, "qualifying_fact_ids": [integers], "best_fact_id": integer or null,
"reasoning": "one or two sentences, including why the selected fact was preferred"}}
"""


def _format_facts_block(facts: list[dict]) -> str:
    if not facts:
        return "(No undisclosed facts remain.)"
    parts = []
    for f in facts:
        parts.append(
            f"- fact_id: {f['fact_id']}\n"
            f"  content: {f['content']}\n"
            f"  reveal_if: {f['reveal_if']}\n"
            f"  do_not_reveal_if: {f['do_not_reveal_if']}"
        )
    return "\n".join(parts)


def _as_int(value: Any) -> int | None:
    return int(value) if str(value).strip().lstrip("-").isdigit() else None


def facilitate(
    adapter: BaseAdapter,
    public_context: str,
    question: str,
    remaining_facts: list[dict],
) -> tuple[str, dict]:
    """Return ``(reply_text, decision)`` for one question.

    ``decision`` always contains: too_broad, qualifying_fact_ids, best_fact_id,
    disclose_fact_ids (0 or 1 element), and reasoning.
    """
    system = FACILITATOR_SYSTEM.format(
        public_context=public_context,
        facts_block=_format_facts_block(remaining_facts),
        question=question,
    )
    raw, _meta = adapter.generate(
        system, [{"role": "user", "content": "Return the JSON decision now."}]
    )

    try:
        parsed = extract_json(raw)
    except ValueError:
        parsed = {
            "too_broad": False,
            "qualifying_fact_ids": [],
            "best_fact_id": None,
            "reasoning": f"(parse failed) {raw[:200]}",
        }

    valid_ids = {f["fact_id"] for f in remaining_facts}
    qualifying = sorted(
        {i for i in (_as_int(x) for x in parsed.get("qualifying_fact_ids", []))
         if i in valid_ids}
    )
    too_broad = bool(parsed.get("too_broad", False))
    best = _as_int(parsed.get("best_fact_id"))
    note = ""

    if too_broad:
        qualifying, best = [], None
        reply = TOO_BROAD_REPLY
    elif not qualifying:
        best = None
        reply = NO_INFO_REPLY
    else:
        if best not in qualifying:
            # Unspecified / invalid / non-qualifying id -> fall back to smallest.
            note = f" [best_fact_id={best!r} corrected]"
            best = qualifying[0]
        by_id = {f["fact_id"]: f for f in remaining_facts}
        reply = by_id[best]["content"]  # single fact only

    decision = {
        "too_broad": too_broad,
        "qualifying_fact_ids": qualifying,
        "best_fact_id": best,
        "disclose_fact_ids": [best] if best is not None else [],
        "reasoning": str(parsed.get("reasoning", "")) + note,
    }
    return reply, decision
