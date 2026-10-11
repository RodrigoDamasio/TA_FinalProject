# Frontend deployment (Vercel): steps for the user

**Deployed 2026-10-11:** https://research-assistant-final-project.vercel.app (Vercel project `research-assistant-final-project`), API https://backend-production-092d.up.railway.app. Backend `FRONTEND_ORIGIN` and `FRONTEND_URL` point at the Vercel URL. Steps below are for redeploys.

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
