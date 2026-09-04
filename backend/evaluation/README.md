# Agent Evaluation Harness

This directory holds regression-style evaluations for the Smart Data Agent.
It is intentionally separate from ``tests/`` (unit/integration tests) and
``app/`` (production code).

## Layout

```
evaluation/
├── datasets/          # Golden test cases (JSONL)
├── metrics/           # Judges / scoring functions
├── prompts/           # Prompts used by LLM-as-a-judge
└── runners/           # Scripts that run an evaluation
```

## Running an evaluation

All commands assume the current working directory is ``backend/`` and the
virtual environment is activated.

```bash
# Run the read_file HIL evaluation
.venv/bin/python -m evaluation.runners.run_eval \
    evaluation/datasets/read_file_cases.jsonl
```

The runner loads each test case, invokes the agent once, and scores:

1. **Tool approval triggered**: did the sensitive ``read_file`` call pause for
   human approval? (only when ``HITL=true``)
2. **Tool call accuracy**: when approved, was the correct file read?
3. **Response relevance**: optional keyword / regex check on the final answer.

## Adding a new evaluation

1. Add golden cases to ``datasets/<name>.jsonl``.
2. Add metric functions to ``metrics/``.
3. Add a runner under ``runners/`` or extend ``run_eval.py``.

## Notes

- Evaluations may call a live LLM and therefore cost tokens.
- Set ``HITL=true`` in ``.env`` when testing the human-in-the-loop flow.
- Long-running evaluations can be pointed at a dedicated checkpointer
  (e.g. ``CHECKPOINTER=sqlite``) so interrupted runs can be resumed.
