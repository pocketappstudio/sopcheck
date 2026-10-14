# Frontend

Next.js 14 (App Router, TypeScript, Tailwind). See ../README.md for setup.

- `/login` - sign in (demo accounts listed on the page)
- `/manager` - Manager Dashboard (managers only)
- `/technician` - Technician View

`/api/*` and `/files/*` are proxied to `BACKEND_URL` (default http://localhost:8000), so the session cookie is first-party.
`middleware.ts` protects routes; the API enforces auth on every call.
