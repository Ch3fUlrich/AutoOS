# Routing map (who creates what)

Keys → providers → combos → routers → IDEs. Every edge names the script or
file that creates it; prose lives where the thing is defined
(`models.md` for chains, `api-keys.md` for keys).

```mermaid
flowchart LR
    K["configuration/api-keys.yml\n(git-ignored SSOT)"]
    K -->|mirror-litellm-env.py| LENV["configuration/litellm/.env"]
    K -->|apply.ps1 / apply.sh| OMPROV["OmniRoute providers\n(openrouter, gemini, groq, ...)"]
    K -->|OpenRouter dashboard BYOK| ORK["OpenRouter provider keys"]
    ORK -->|paid legs| COMBO
    LENV -->|start-litellm.ps1| LITE["LiteLLM :4000\n(manual fallback)"]
    C["configuration/omniroute/combos.json\n(SSOT: leg order)"] -->|apply.ps1 / apply.sh| COMBO["OmniRoute combos :20128\ntier1/2/3, pinned singles"]
    C -->|sync-router-tiers.py --check| LITE
    COMBO --> OC["opencode.jsonc providers.omniroute\n(opencode CLI/TUI/Desktop, serve)"]
    LITE --> OCLITE["opencode.jsonc providers.litellm"]
    COMBO --> OH["tier-profiles.json\n→ sync-openhands-profiles.py\n→ OpenHands :3000"]
    COMBO --> ZED["lib installers Zed writer\n→ autoos-omniroute :20128"]
    OC --> NVIM["nvim + sidekick\n(inherits opencode routing)"]
    SK[".agents/skills/\n(single home)"] -->|installer junctions| CL[".claude/skills/\n(Claude Code)"]
    SK -->|load_skills_from_dir| OH
```

Drift gates (run after any combo/client edit): `audit-router.py` (live) /
`--offline` (CI), `sync-router-tiers.py --check`, `check-links.py`.

A live probe that gets HTTP 503 is retried with a backoff (5 s, 15 s, 45 s) before it is
reported: OmniRoute answers 503 "resource pressure" when the host is short of memory, which
is load shedding, not a dead leg. 400, 404 and transport errors are drift and are never
retried, so the audit stays quick when a leg is really gone. A leg that still answers 503
after the last retry is reported as `503` (state, not drift) and the audit does not fail.
