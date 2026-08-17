# Running the frontend locally

## Prerequisites

- Node.js 20+ (this machine has v22.8.0, confirmed working)
- The backend running somewhere reachable — either:
  - **Local backend** (`retrieval_layer/api_server.py` on `localhost:8000`) — needs
    Redis running locally too (`sudo systemctl start redis-server`), and the
    Python venv at `Stage 1/venv` set up with `Stage 1/requirements.txt`
    installed. This is what local dev has been using all session.
  - **Deployed backend** (Render/HF Spaces URL) — no local Python/Redis needed
    at all, see step 2 below.

## 1. Install dependencies

```bash
cd "/home/vulcan/Documents/Projects/Research Maker/frontend"
npm install
```

## 2. Point at the right backend

By default the frontend calls `http://localhost:8000` (see
`frontend/src/lib/api/client.ts`). To use a different backend (e.g. your
deployed Render/HF Space URL instead of a local one), create
`frontend/.env.local`:

```bash
echo "NEXT_PUBLIC_API_BASE_URL=https://your-backend-url" > frontend/.env.local
```

Skip this step entirely if you're running the backend locally on the
default port 8000 — no `.env.local` needed.

## 3. Start the backend (only if running it locally)

In a separate terminal:

```bash
cd "/home/vulcan/Documents/Projects/Research Maker/retrieval_layer"
source "../Stage 1/venv/bin/activate"
uvicorn api_server:app --reload --port 8000
```

Confirm it's up: `curl http://localhost:8000/api/health` → `{"status":"ok"}`

## 4. Start the frontend

```bash
cd "/home/vulcan/Documents/Projects/Research Maker/frontend"
npm run dev
```

Open **http://localhost:3000**.

## 5. Verify it's actually working

- Send a chat message — you should get a real answer (not a "Couldn't reach
  the backend" toast).
- Open Knowledge Bases in the sidebar — should list real files, not an
  empty/error state.
- Ask a follow-up like "explain that in more detail" — should get a grounded
  answer, not something unrelated (this is the conversation-memory fix from
  this session; if it regresses, the backend's `session_id` wiring broke).

## Production build (optional)

To test the actual production build instead of the dev server:

```bash
cd "/home/vulcan/Documents/Projects/Research Maker/frontend"
npm run build
npm run start
```

Still serves on port 3000 by default.

## Common issues

- **"Couldn't reach the backend — is api_server.py running on port 8000?"**
  toast — the backend isn't reachable at whatever `NEXT_PUBLIC_API_BASE_URL`
  resolves to. Check step 2/3.
- **CORS error in the browser console** — only relevant if pointing at a
  deployed backend; its `ALLOWED_ORIGINS` env var needs
  `http://localhost:3000` in the list too if you want to test the deployed
  backend from your local frontend.
