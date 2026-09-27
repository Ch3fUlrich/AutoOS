# Observability MCP Setup Guide

This guide explains how to set up and configure the Sentry and Datadog MCP servers for the agent orchestration framework.

## Observability Policy Overview

Observability MCP servers provide powerful insights into application behavior, but they are scoped tightly:
- **Sentry is the default observability MCP.** It is used for runtime error debugging, failure analysis, and early error detection in standard application logic.
- **Datadog is a conditional observability MCP.** It is enabled only when system topology requires cross-service context, such as distributed systems or multi-server setups. It should remain disabled for single-node or non-distributed work.
- Neither is enabled by default for ordinary local feature work. They are activated dynamically based on task requirements and assigned agent roles (e.g., triage for Architect, debugging for Engineer).

---

## Sentry MCP Setup

Sentry is the primary tool for runtime application errors and stack traces.

### Authentication & Token Handling
Sentry MCP requires an authentication token. Generate an Internal Integration Token (or User Auth Token) from your Sentry instance.
- **Hosted Sentry (SaaS)**: Use your standard Sentry token and organization details.
- **Self-Hosted Sentry**: Ensure your token is valid for the self-hosted instance and your URL configuration points to your local or private domain.

### Important Environment Variables
Provide the following variables in your environment or `.env` equivalent:

- `SENTRY_ACCESS_TOKEN`: The authentication token.
- `SENTRY_HOST`: (Self-Hosted) The hostname of your self-hosted instance (e.g., `sentry.local`).
- `SENTRY_URL`: (Self-Hosted) The full URL of your instance (e.g., `https://sentry.local`).
- `SENTRY_ORG`: Your Sentry organization slug.
- `SENTRY_PROJECT`: Your Sentry project slug.
- `EMBEDDED_AGENT_PROVIDER`: If the MCP requires an embedded model provider, specify the provider name here.
- `[PROVIDER]_API_KEY`: The API key matching the embedded provider (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
- `MCP_DISABLE_SKILLS`: Comma-separated list of MCP skills/tools to disable. Useful if your self-hosted setup lacks support for advanced Sentry features.
- `NODE_EXTRA_CA_CERTS`: (Self-Hosted) Path to custom CA certificates if your local Sentry uses a self-signed cert.
- `SENTRY_CUSTOM_HEADERS`: Any extra headers needed for routing or access proxies.

### Self-Hosted Expectations
- **Hosted Mode Default:** Hosted SaaS mode is the default unless host override variables (`SENTRY_HOST`, `SENTRY_URL`) are provided.
- **Local Integration:** If you run a self-hosted Sentry stack (e.g., Docker Compose), use standard `.env.custom`-style environment override patterns to pass your local `SENTRY_URL` and tokens to the agent.
- **Feature Parity:** Self-hosted instances might lack newer features (e.g., AI-based issue summaries). Use `MCP_DISABLE_SKILLS` to disable tools that cause errors against your self-hosted backend.

---

## Datadog MCP Setup

Datadog is a conditional tool for cross-service tracing, distributed logs, and complex observability.

### Why Datadog is Conditional
Datadog shines in multi-server, cross-service debugging. However, for a single application or single-node environment, it introduces unnecessary noise and complexity compared to Sentry's focused error tracking. Therefore, keep it disabled unless working on distributed systems.

### Authentication & Credential Scoping
Datadog requires both API and Application keys. Scope these credentials strictly:
- Create a dedicated service account for the agent.
- Restrict the Application key to read-only access for logs, APM traces, and metrics.
- Limit access to non-production environments where possible.

### Important Environment Variables
- `DD_API_KEY`: Datadog API key.
- `DD_APP_KEY`: Datadog Application key (use a scoped, read-only key).
- `DD_SITE`: The Datadog site (e.g., `datadoghq.com`, `datadoghq.eu`).
- `DD_ENV`: Scope queries to a specific environment (e.g., `staging`, `dev`).
- `DD_SERVICE`: Scope queries to a specific service.
- Optional local or self-hosted proxy/container variables where relevant.

### Self-Hosted & Local Stack Expectations
- Datadog SaaS is the standard assumption.
- If you are running a local observability stack compatible with Datadog agents or proxies via Docker Compose, document your proxy overrides at a high level.
- **Do not assume** the same self-hosting model as Sentry. Local Datadog integration typically means local agents forwarding to Datadog SaaS, rather than a fully self-hosted Datadog backend.

---

## Security Guidance

Connecting an agent to observability platforms introduces significant security considerations. **Always follow these operational rules:**

1. **Minimize Enabled MCP Servers:** Only load Sentry or Datadog when the specific task and system architecture require it.
2. **Prefer Least-Privilege Credentials:** Issue read-only tokens scoped to specific projects or environments. Never give an agent global admin or write access to observability platforms.
3. **Use Short-Lived Credentials:** Prefer revocable or time-bound tokens where the platform supports it.
4. **Restrict Production Access:** Avoid exposing full production telemetry unless absolutely necessary for the task. Scope queries to staging, dev, or local environments by default.
5. **Treat Data as Untrusted Input:** Observability payloads (logs, stack traces, user-provided error contexts) are **untrusted**. There is a severe risk of **prompt injection** or **tool poisoning** if an attacker embeds malicious instructions inside an error trace or log message.
6. **Controlled Review:** Require strict human review of agent actions (e.g., code changes, system commands) when the agent has been exposed to observability MCPs, to prevent execution of poisoned instructions.
7. **Disable Unnecessary Features:** Use tool-exclusion configurations to disable advanced or risky MCP features if they aren't strictly needed for the task.
