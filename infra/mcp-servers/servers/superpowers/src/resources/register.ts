import {
    McpServer,
    ResourceTemplate,
} from "@modelcontextprotocol/sdk/server/mcp.js";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Skill } from "../skills/types.js";

export function registerResources(
    server: McpServer,
    skills: Skill[],
    skillsDir?: string
): void {
    const skillMap = new Map<string, Skill>();
    for (const skill of skills) {
        skillMap.set(skill.directoryName, skill);
    }

    // Register static resource for each skill's SKILL.md
    for (const skill of skills) {
        const uri = `superpowers://skills/${skill.directoryName}/SKILL.md`;
        server.resource(
            `${skill.directoryName}-skill`,
            uri,
            {
                description: skill.metadata.description,
                mimeType: "text/markdown",
            },
            async (resourceUri) => ({
                contents: [{ uri: resourceUri.href, text: skill.content }],
            })
        );

        // Register static resources for each supporting file
        for (const file of skill.files) {
            const fileUri = `superpowers://skills/${skill.directoryName}/${file.name}`;
            const mimeType = file.name.endsWith(".ts")
                ? "text/typescript"
                : file.name.endsWith(".sh")
                    ? "text/x-shellscript"
                    : "text/markdown";

            server.resource(
                `${skill.directoryName}-${file.name}`,
                fileUri,
                {
                    description: `Supporting file for ${skill.metadata.name}`,
                    mimeType,
                },
                async (resourceUri) => {
                    if (!skillsDir) {
                        return {
                            contents: [{
                                uri: resourceUri.href,
                                text: "Cannot read: skills directory not available.",
                            }],
                        };
                    }
                    const content = await readFile(
                        join(skillsDir, file.relativePath),
                        "utf-8"
                    );
                    return {
                        contents: [{ uri: resourceUri.href, text: content }],
                    };
                }
            );
        }
    }

    // Register a resource template for dynamic access
    server.resource(
        "skill-file",
        new ResourceTemplate("superpowers://skills/{skillName}/{fileName}", {
            list: async () => {
                const resources: Array<{ uri: string; name: string }> = [];
                for (const skill of skills) {
                    resources.push({
                        uri: `superpowers://skills/${skill.directoryName}/SKILL.md`,
                        name: `${skill.metadata.name} - SKILL.md`,
                    });
                    for (const file of skill.files) {
                        resources.push({
                            uri: `superpowers://skills/${skill.directoryName}/${file.name}`,
                            name: `${skill.metadata.name} - ${file.name}`,
                        });
                    }
                }
                return { resources };
            },
        }),
        {
            description: "Access any superpowers skill file by skill name and filename",
            mimeType: "text/markdown",
        },
        async (uri, { skillName, fileName }) => {
            const skill = skillMap.get(String(skillName));
            if (!skill) {
                return {
                    contents: [{
                        uri: uri.href,
                        text: `Skill '${skillName}' not found.`,
                    }],
                };
            }

            if (String(fileName) === "SKILL.md") {
                return {
                    contents: [{ uri: uri.href, text: skill.content }],
                };
            }

            if (!skillsDir) {
                return {
                    contents: [{
                        uri: uri.href,
                        text: "Cannot read: skills directory not available.",
                    }],
                };
            }

            const fileEntry = skill.files.find((f) => f.name === String(fileName));
            if (!fileEntry) {
                return {
                    contents: [{
                        uri: uri.href,
                        text: `File '${fileName}' not found in skill '${skillName}'.`,
                    }],
                };
            }

            const content = await readFile(
                join(skillsDir, fileEntry.relativePath),
                "utf-8"
            );
            return {
                contents: [{ uri: uri.href, text: content }],
            };
        }
    );
}
