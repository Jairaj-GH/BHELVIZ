# BHELVIZ Frontend

Development notes for the frontend chat UI.

Prerequisites:
- Node 18+ and npm or yarn

Install:

```bash
cd frontend
npm install
```

Run dev server:

```bash
npm run dev
```

The frontend expects the backend API at the same origin (`/query`, `/auth/token`). For local offline development without the backend, the chat widget falls back to a demo response when the network call fails.

Build for production:

```bash
npm run build
```

Preview the production build:

```bash
npm run preview
```

Notes:
- The chat widget persists conversation history to `localStorage` under `bhelviz_chat_history`.
- To test full backend integration, run the backend API and ensure CORS and auth tokens are configured.
