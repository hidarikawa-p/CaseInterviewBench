You are participating in an interactive task.

You will receive an initial situation and request. You may ask the interviewer questions before providing your final answer.

## 1. Initial Information

### Public Context

[PUBLIC_CONTEXT]

### Initial Request

[INITIAL_REQUEST]

## 2. Interaction Procedure

You may ask the interviewer up to 20 questions.

Follow all of the rules below:

* Each message during the question phase must contain exactly one question.
* Each question must be written as exactly one sentence.
* Each question must ask about only one substantive issue.
* Do not combine multiple questions in one sentence.
* Do not use a list of questions.
* Do not ask for multiple independent categories of information in a single question.
* Do not ask the interviewer to provide the final answer.
* You do not need to use all 20 questions.
* When you decide to stop asking questions, submit the final answer in the required format.
* After submitting the final answer, do not ask any further questions.
* If you use all 20 questions, submit the final answer after receiving the response to the twentieth question.

A question concerns more than one substantive issue when separate answers could reasonably be given to different parts of the sentence.

For example, the following asks about one issue:

`Which group is most affected by the recent change?`

The following asks about two independent issues and is not allowed:

`Which group is most affected, and what has been tried so far?`

## 3. Question-Phase Output

During the question phase, output only one question.

Do not include:

* analysis
* explanations
* summaries
* hypotheses
* headings
* numbering
* XML tags
* JSON
* proposed conclusions
* any text before or after the question

End the message with a question mark.

## 4. Ending the Question Phase

When you stop asking questions, output the final answer instead of another question.

The final-answer turn must contain exactly one `<conclusion>` block.

Place one valid JSON object between `<conclusion>` and `</conclusion>`.

Once the `<conclusion>` block has been submitted, the interaction is complete.

## 5. Final-Answer Format

Use exactly the following structure:

<conclusion>
{
  "problem_formulation": "",
  "problem_evidence": "",
  "resolution": "",
  "resolution_rationale": ""
}
</conclusion>

Follow all of these formatting rules:

* Do not include any text before or after the `<conclusion>` block.
* Do not use Markdown code fences.
* Do not add keys other than the four specified keys.
* Use double quotation marks for every key and string value.
* Do not use `null`.
* Do not use trailing commas.
* Ensure that the content inside the block is syntactically valid JSON.
* Each string value must contain no more than 100 words.

## 6. Final-Answer Fields

### problem_formulation

State the problem or issue that should be addressed.

### problem_evidence

State the evidence supporting the problem formulation.

### resolution

State the proposed resolution, conclusion, recommendation, or course of action.

### resolution_rationale

State the evidence or reasoning supporting the resolution.

## 7. Final Output Check

Before submitting a question, verify that it:

1. Is exactly one sentence.
2. Contains exactly one question.
3. Concerns only one substantive issue.
4. Contains no additional text.

Before submitting the final answer, verify that it:

1. Contains exactly one `<conclusion>` block.
2. Contains valid JSON inside the block.
3. Contains exactly the four required keys.
4. Keeps every field within 100 words.
5. Contains no text outside the block.
