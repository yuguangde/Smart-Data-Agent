# Smart Data Agent — Backend

LangGraph-powered chatbot backend with FastAPI, tool calling, persistent memory, streaming responses and WebSocket transport.
Supports OpenAI / Anthropic / DeepSeek / Qwen out of the box.

> Built on **LangGraph + LangChain + FastAPI**, follows the [official LangGraph quickstart](https://langchain-ai.github.io/langgraph/) and gives you a battery-included HTTP/WebSocket service around it.

---

## ✨ Features

| Area              | What's included                                                              |
|-------------------|------------------------------------------------------------------------------|
| **Reasoning**     | LangGraph ReAct-style agent with tool calling loops                           |
| **Models**        | OpenAI, Anthropic Claude, DeepSeek, Qwen DashScope (OpenAI-compatible clients)|
| **Tools**         | `calculator`, `get_current_time`, `web_search`, `knowledge_search` (extendable)|
| **Memory**        | In-memory (dev) or SQLite checkpointer for multi-turn persistence             |
| **Streaming**     | Server-Sent Events (token-level) and WebSocket bidir streams                   |
| **HITL**          | Optional human-in-the-loop interrupt before the agent node runs               |
| **Observability** | LangSmith tracing optional via env vars                                       |
| **Deployment**    | Dockerfile + docker-compose ready; CORS configurable                          |

---

## 🗂️ Project Layout

```
backend/
├── app/
│   ├── main.py              # FastAPI factory + lifespan
│   ├── config.py            # pydantic-settings (.env)
│   ├── agent/               # LangGraph: graph, nodes, state, prompts
│   ├── llm/                 # LLM provider factory (OpenAI / Anthropic / DeepSeek / Qwen)
│   ├── memory/              # Checkpointer (memory / sqlite)
│   ├── tools/               # LangChain tools registered into the agent
│   ├── services/            # Service layer wrapping the compiled graph
│   └── api/                 # FastAPI routers (chat, ws, schemas)
├── tests/                   # Pytest suite (wiring + pure tool tests)
├── data/knowledge/          # Drop .md / .txt files for the knowledge_search tool
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── Makefile
```

---

## ⚡ Quick Start

### 1. Install

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in OPENAI_API_KEY (or one of the other providers) inside .env
```

### 2. Run

```bash
# A. Local dev (hot reload)
uvicorn app.main:app --reload --port 8000
# or
make run

# B. Docker
docker build -t smart-data-agent-backend .
docker run --rm -p 8000:8000 --env-file .env smart-data-agent-backend
# or
make docker-up
```

Open <http://localhost:8000/docs> for interactive Swagger UI.

---

## 🔌 HTTP API

### `POST /chat` — single answer

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "thread_id": "demo-1",
    "message": "What is 42 * 17? Use the calculator tool.",
    "user_id": "u-001"
  }'
```

**Response**
```json
{
  "thread_id": "demo-1",
  "message":    {"role": "assistant", "content": "42 * 17 = 714"},
  "iterations": 2,
  "tool_calls": [
    {"name": "calculator", "args": {"expression": "42 * 17"}, "result": "714"}
  ]
}
```

### `POST /chat/stream` — Server-Sent Events

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message": "Stream me a haiku about Python."}'
```

**Event types**: `message` → `token` ×N → (optional) `tool_start` / `tool_end` → `done` → `end`

### `POST /threads` — new conversation

```bash
curl -X POST http://localhost:8000/threads
# {"thread_id": "abc123...", "created_at": "..."}
```

### `GET /threads/{id}` — history

```bash
curl http://localhost:8000/threads/demo-1
```

### `GET /health` — liveness

```bash
curl http://localhost:8000/health
# {"status":"ok","llm_provider":"openai","checkpointer":"memory","hitl":false}
```

---

## 🔁 WebSocket API

Endpoint: `ws://localhost:8000/ws/chat`

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8000/ws/chat") as ws:
        await ws.send(json.dumps({"type": "message", "content": "hi"}))
        async for raw in ws:
            print(json.loads(raw))  # {"type":"thread"...}, {"type":"token","data":"..."}, ...

asyncio.run(main())
```

Inbound frames: `{"type":"message"|"history"|"reset"|"ping", ...}`
Outbound frames: `{"type":"thread"|"token"|"tool_start"|"tool_end"|"done"|"error"|"history"|"pong", ...}`

---

## ⚙️ Configuration

All configuration is 12-factor via `.env`:

| Variable              | Default              | Notes                                           |
|-----------------------|----------------------|--------------------------------------------------|
| `LLM_PROVIDER`        | `openai`             | `openai` / `anthropic` / `deepseek` / `qwen`     |
| `OPENAI_API_KEY`      | —                    |                                                  |
| `OPENAI_MODEL`        | `gpt-4o-mini`        |                                                  |
| `OPENAI_TEMPERATURE`  | `0.7`                |                                                  |
| `ANTHROPIC_API_KEY`   | —                    | When `LLM_PROVIDER=anthropic`                    |
| `ANTHROPIC_MODEL`     | `claude-sonnet-4-5`  |                                                  |
| `DEEPSEEK_API_KEY`    | —                    | When `LLM_PROVIDER=deepseek`                     |
| `QWEN_API_KEY`        | —                    | DashScope; when `LLM_PROVIDER=qwen`              |
| `MAX_TOKENS`          | `2048`               |                                                  |
| `MAX_ITERATIONS`      | `8`                  | Hard cap on ReAct loop iterations                |
| `CHECKPOINTER`        | `memory`             | `memory` (dev) or `sqlite` (persistent)          |
| `SQLITE_PATH`         | `./data/chat.db`     |                                                  |
| `HITL`                | `false`              | Interrupt before agent node                      |
| `HOST` / `PORT`       | `0.0.0.0` / `8000`   |                                                  |
| `LOG_LEVEL`           | `INFO`               |                                                  |
| `LANGSMITH_TRACING`   | `false`              | Set `true` + `LANGSMITH_API_KEY` to enable       |

---

## 🛠️ Adding a New Tool

1. Drop a file in `app/tools/`, e.g. `app/tools/my_tool.py`:

   ```python
   from langchain_core.tools import tool

   @tool
   def my_tool(arg: str) -> str:
       """One-line description the LLM sees."""
       return f"got: {arg}"
   ```

2. Re-export it from `app/tools/__init__.py`:

   ```python
   from app.tools.my_tool import my_tool
   ALL_TOOLS = [..., my_tool]
   ```

Restart the service. The agent will auto-bind the new tool — no graph changes required.

---

## 🧪 Development

```bash
make lint     # ruff check
make format   # ruff format + import sort
make test     # pytest (runs wiring + pure tool tests; no live LLM)
```

Add new tests under `tests/`. Keep state-dependent tests isolated with small fixtures.

---

## 🐳 Docker

```bash
docker build -t smart-data-agent-backend .                        # build image
docker compose up -d                                              # start persistent stack
docker compose logs -f api                                        # tail logs
docker compose down                                              # stop + remove containers
```

The container exposes `8000`, persists `./data/chat.db`, and runs uvicorn with 4 workers.

---

## 🧠 How It Works

```
                ┌──────────────────────────┐
   user query   │  POST /chat or /ws/chat   │   tool calls (SSE token stream)
   ─────────►   │  FastAPI  ─►  Service    │  ───────────────────────────────►
                │              ─►  LangGraph│
                │                  agent ⇄ tools
                │                  ⇣        │
                │           (memory / sqlite)│
                └──────────────────────────┘
```

1. `app/services/agent_service.py` accepts the user message and threads it through LangGraph.
2. `app/agent/graph.py` builds a `StateGraph` with the LLM node + `ToolNode`, optionally with `interrupt_before="agent"` for HITL.
3. Tools execute against the sandboxed registry in `app/tools/`; tool calls are streamed back to the client.
4. Checkpointing persists conversation state so `thread_id` continues between calls.

---

## 📜 License

MIT. See `LICENSE`.