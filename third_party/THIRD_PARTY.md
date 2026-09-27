# Third-Party Components

Register of vendored upstream code that lives outside `third_party/` proper and
keeps its own licence. Each entry names the source of every claim so it can be
re-checked; none is inferred.

## superpowers-mcp

- **Package**: `superpowers-mcp` (source: `package.json` `"name"`)
- **Version**: `0.1.0` (source: `package.json` `"version"`)
- **Upstream repo**: https://github.com/erophames/superpowers-mcp (source: `infra/mcp-servers/servers/superpowers/README.md:12` clone URL)
- **Packages the skills of**: https://github.com/obra/superpowers (source: `README.md:3` and `src/git.ts:6`, the repo whose skill set this server serves)
- **License**: MIT — declared in `package.json` `"license"`. The upstream `LICENSE` file was **not** present in the import tree, so its text is deliberately not reproduced here (no fabricated copyright line). Add the verbatim upstream `LICENSE` if it becomes available.
- **Location**: `infra/mcp-servers/servers/superpowers/`
- **Description**: Node.js MCP server exposing "superpowers" coding-workflow skills (TDD, debugging, planning, etc.) to any LLM client.
- **Notes**: Vendored copy of upstream MIT-licensed code. `package.json` declares `"license": "MIT"`; the upstream repository's `LICENSE` file was not included in the import.