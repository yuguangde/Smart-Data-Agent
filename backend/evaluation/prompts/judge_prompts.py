"""LLM-as-a-judge prompts."""
from __future__ import annotations


READ_FILE_JUDGE = """\
You are evaluating whether an AI agent correctly answered a user's question.

User question:
{question}

Expected file to read:
{expected_path}

Agent's final answer:
{answer}

Score the answer as PASS or FAIL and explain why.
A PASS means the answer clearly shows the agent read the requested file and
summarised or quoted its contents accurately.
A FAIL means the answer does not demonstrate the file was read.
""".strip()


def build_read_file_judge_prompt(question: str, expected_path: str, answer: str) -> str:
    return READ_FILE_JUDGE.format(
        question=question,
        expected_path=expected_path,
        answer=answer,
    )
