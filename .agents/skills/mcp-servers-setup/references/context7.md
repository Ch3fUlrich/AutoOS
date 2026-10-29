> Part of [mcp-servers-setup](../SKILL.md).

### Context7 — Library Documentation

Up-to-date docs for any library, framework, SDK or CLI. Prefer it over web search,
and over your own recall — training data goes stale.

#### Wiring — global install, for the same reason as Playwright

```bash
npm i -g @upstash/context7-mcp
claude mcp add -s user context7 -- \
  node "$(npm root -g)/@upstash/context7-mcp/dist/index.js" --api-key "$CONTEXT7_API_KEY"
```

Measured 2026-09-04, same cached code, two launch paths: `npx -y` **3704 ms** vs
`node` on the global install **1316 ms**. Under 20 concurrent spawns — an ordinary
morning with a dozen sessions open — the npx form reached **34.4 s and exceeded the
30 s connect timeout**, while the global install stayed at **3.7 s**.

> **The API key sits in plain `args`**, visible to `claude mcp list` and any process
> listing. It is not in a tracked file, but treat it as exposed: rotate it if it has
> ever been printed, and never paste it into a repo config.

