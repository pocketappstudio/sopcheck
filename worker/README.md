# SOPCheck AI proxy (Cloudflare Worker)

Lets the static GitHub Pages demo use real gpt-4.1 parsing while `OPENAI_API_KEY` stays a Worker secret (never in the repo or browser).

- Accepts only `POST /parse` with the exact SOP-extraction messages (system prompt `sop-extract/1.2.0`, templated user + repair prompts).
- CORS: only `https://pocketappstudio.github.io`. Limits: 10 calls/hour and 30/day per IP; global spend cap `DAILY_CAP_USD` (0.50) per UTC day (KV).
- The demo runs the backend pipeline (chunk/validate/repair/merge) in the browser; if the Worker fails or the cap is hit it falls back to the offline parser.
- `npm test` (offline unit tests), `LIVE=1 npm test` (one real call), `npm run prompts` (regenerate `src/prompts.js` from `prompts/system_prompt.md`).
- Deploy: `../deploy-worker.sh` with `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `OPENAI_API_KEY`, `GITHUB_TOKEN_ROWBIN` in the environment.

Token (one click to pre-fill): https://dash.cloudflare.com/profile/api-tokens?permissionGroupKeys=%5B%7B%22key%22%3A%22workers_scripts%22%2C%22type%22%3A%22edit%22%7D%2C%7B%22key%22%3A%22workers_kv_storage%22%2C%22type%22%3A%22edit%22%7D%2C%7B%22key%22%3A%22account_settings%22%2C%22type%22%3A%22read%22%7D%5D&accountId=%2A&zoneId=all&name=SOPCheck%20Worker%20deploy
(If the permission dropdowns look empty, click "Edit token" once, a known dashboard quirk.) Account ID: Workers & Pages page, "Account Details" panel.
