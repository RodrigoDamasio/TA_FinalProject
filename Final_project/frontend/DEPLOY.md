# Frontend deployment (Vercel): steps for the user

**Not deployed yet.** Cloud sessions never deploy; these steps are run locally by the user (FRONTEND_PLAN §7). The Module 5 deployment notes this file replaced are in the TA_Module5 repo.

```bash
# gates first
npm run typecheck && npm run lint && npm run test:coverage && npm run test:e2e

# 1. Vercel project and the API URL (inlined at BUILD time)
vercel link --yes --project research-assistant-final-project
printf 'https://<backend>.up.railway.app' | vercel env add NEXT_PUBLIC_API_URL production
vercel --prod --yes

# 2. Backend variables on Railway, then redeploy the backend
#    FRONTEND_ORIGIN = the Vercel origin (CORS), FRONTEND_URL = the Vercel URL (links in Telegram and callbacks)

# 3. Post-deploy checks
BASE_URL=https://<vercel-domain> npm run test:e2e        # health + axe; a real run only with E2E_REAL_RUN=1
```

- Changing `NEXT_PUBLIC_API_URL` needs `vercel env rm/add` and a new `vercel --prod` (the CSP `connect-src` is computed from it at build time too).
- `next build` type-checks only the app (`tsconfig.build.json` excludes `tests/` and `e2e/`); `npm run typecheck` checks everything.
- Vercel holds no secret.
- Lighthouse accessibility ≥ 95 on the deployed URL (FRONTEND_PLAN §7).
