# Smart Data Agent — Frontend

A simple Agent Web UI for the **Smart Data Agent** backend, built with
**React 18 + Vite 5 + TypeScript** and **Ant Design X**.

It talks to the FastAPI backend (`/chat`, `/chat/stream`, `/threads/{id}`)
over Server-Sent Events and renders streaming replies token-by-token while
visualising tool calls captured from `tool_start` / `tool_end` frames.

## Features

- 💬 Multi-turn chat with token-level streaming (SSE).
- 📂 Persistent sidebar of conversations (saved in `localStorage`).
- 🛠 Tool-call visualisation (each `tool_start` / `tool_end` becomes a
  collapsible card under the assistant bubble).
- 🆕 Brand-new conversation button (creates a server-side `thread_id`).
- ⏹ Stop button — aborts the active SSE stream via `AbortController`.
- 🌗 Modern Ant Design X look & feel, Chinese locale.

## Quick Start

### 1. Start the backend

From the project root:

```bash
cd ../backend
pip install -r requirements.txt
cp .env.example .env   # edit if needed (LLM keys, etc.)
python run.py          # or: uvicorn app.main:app --reload
```

The backend listens on `http://localhost:8000` and exposes:

| Method | Path                | Purpose                                     |
| ------ | ------------------- | ------------------------------------------- |
| POST   | `/threads`          | Create a new conversation thread            |
| GET    | `/threads/{id}`     | Fetch a thread's message history            |
| POST   | `/chat`             | Blocking chat (full reply)                  |
| POST   | `/chat/stream`      | **SSE stream** used by this UI              |
| GET    | `/health`           | Liveness probe                              |

### 2. Start the frontend

```bash
npm install
npm run dev
```

Open <http://localhost:5173>.

Vite proxies `/api/*` to the backend (see `vite.config.ts`), so the UI
talks to it via same-origin URLs.

### 3. Production build

```bash
npm run build
npm run preview
```

## Project Layout

```
frontend/
├── index.html
├── package.json
├── tsconfig*.json
├── vite.config.ts
├── .env                       # VITE_API_BASE=/api by default
└── src/
    ├── main.tsx               # React entry
    ├── App.tsx                # Top-level layout
    ├── api/chat.ts            # HTTP + SSE client
    ├── types/chat.ts          # Wire types
    ├── store/useChatStore.ts  # useReducer + streaming buffers
    ├── components/
    │   ├── Sidebar.tsx        # Conversation list (@ant-design/x)
    │   ├── ChatList.tsx       # Bubble list + tool-call cards
    │   └── SenderBox.tsx      # Sender input + stop button
    └── style/global.css
```

## How streaming works

`useChatStore` uses a single `useReducer` for canonical state, plus three
refs (`assistantIdRef`, `streamBufferRef`, `toolCallsRef`) that hold
streaming-only buffers. Each receiving `token` frame appends to the
buffer and then *replaces* the assistant bubble's content with the
running total — this avoids dispatching once per token (which would
re-render the entire list per token).

The SSE parser in `api/chat.ts` reads `text/event-stream` frames, splits
on the blank-line separator, and JSON-decodes the `data:` payload when
the server sends a structured payload.

## Configuration

`.env`:

```bash
# In dev, vite proxies /api to http://localhost:8000 — leave as-is.
# In production, set this to your backend origin, e.g. https://api.example.com
VITE_API_BASE=/api
```

## Scripts

| Script              | Description                          |
| ------------------- | ------------------------------------ |
| `npm run dev`       | Vite dev server (HMR, proxies /api)  |
| `npm run build`     | Type-check + production bundle       |
| `npm run preview`   | Serve the built bundle locally       |
| `npm run type-check`| `tsc --noEmit` against `tsconfig.app`|