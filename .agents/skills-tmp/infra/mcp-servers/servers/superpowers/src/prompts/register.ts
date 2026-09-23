import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { Skill } from "../skills/types.js";

export function registerPrompts(server: McpServer, skills: Skill[]): void {
    for (const skill of skills) {
        server.prompt(
            `superpowers:${skill.directoryName}`,
            skill.metadata.description,
            () => ({
                messages: [
                    {
                        role: "user" as const,
                        content: {
                            type: "text" as const,
                            text: skill.content,
                        },
                    },
                ],
            })
        );
    }
}
