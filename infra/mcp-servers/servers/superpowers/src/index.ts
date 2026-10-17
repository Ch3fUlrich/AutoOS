#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createSuperpowersServer } from "./server.js";
import { runSetup } from "./cli/setup.js";
import { getSkillsDir } from "./config.js";
import { checkAndApplyUpdates } from "./update.js";
import { existsSync } from "node:fs";
import { join } from "node:path";

async function main(): Promise<void> {
    if (process.stdout.isTTY) {
        await runSetup();
    }

    await checkAndApplyUpdates();

    const skillsDir = getSkillsDir();
    let effectiveSkillsDir = skillsDir;
    if (skillsDir && existsSync(join(skillsDir, "skills"))) {
        effectiveSkillsDir = join(skillsDir, "skills");
    }

    const { server } = await createSuperpowersServer({ skillsDir: effectiveSkillsDir });
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("superpowers-mcp: server running on stdio");
}

main().catch((error) => {
    console.error("superpowers-mcp: fatal error:", error);
    process.exit(1);
});
