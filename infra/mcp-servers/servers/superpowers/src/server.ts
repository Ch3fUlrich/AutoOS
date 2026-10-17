import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import {
    discoverSkillsFromDirectory,
    resolveSkillsDirectory,
} from "./skills/discovery.js";
import { registerTools } from "./tools/register.js";
import { registerPrompts } from "./prompts/register.js";
import { registerResources } from "./resources/register.js";
import type { Skill } from "./skills/types.js";

export interface ServerOptions {
    skillsDir?: string;
}

export interface ServerResult {
    server: McpServer;
    skills: Skill[];
    skillsDir: string | null;
}

export async function createSuperpowersServer(
    options: ServerOptions = {}
): Promise<ServerResult> {
    const skillsDir =
        options.skillsDir ?? resolveSkillsDirectory(process.env) ?? null;

    let skills: Skill[] = [];
    if (skillsDir) {
        skills = await discoverSkillsFromDirectory(skillsDir);
        console.error(
            `superpowers-mcp: discovered ${skills.length} skills from ${skillsDir}`
        );
    } else {
        console.error(
            "superpowers-mcp: no skills directory found, no skills loaded"
        );
    }

    const server = new McpServer({
        name: "superpowers-mcp",
        version: "0.1.0",
    });

    registerTools(server, skills, skillsDir ?? undefined);
    registerPrompts(server, skills);
    registerResources(server, skills, skillsDir ?? undefined);

    return { server, skills, skillsDir };
}
