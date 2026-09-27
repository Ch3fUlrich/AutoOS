import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Skill } from "../skills/types.js";
import {
    buildSkillIntelligenceIndex,
    composeWorkflow,
    recommendSkills,
    semanticSearchSkills,
    validateNextSkill,
    validateWorkflow,
} from "./intelligence.js";

export function registerTools(
    server: McpServer,
    skills: Skill[],
    skillsDir?: string
): void {
    const skillMap = new Map<string, Skill>();
    for (const skill of skills) {
        skillMap.set(skill.directoryName, skill);
    }
    const availableSkillNames = new Set(skills.map((skill) => skill.directoryName));
    const sessionSkillHistory: string[] = [];
    let intelligenceIndexPromise: Promise<Awaited<ReturnType<typeof buildSkillIntelligenceIndex>>> | null = null;

    const getIntelligenceIndex = async () => {
        intelligenceIndexPromise ??= buildSkillIntelligenceIndex(skills, skillsDir);
        return intelligenceIndexPromise;
    };

    server.tool(
        "list_skills",
        "List all available superpowers skills with their descriptions and supporting files",
        async () => {
            const listing = skills.map((s) => ({
                name: s.directoryName,
                displayName: s.metadata.name,
                description: s.metadata.description,
                files: s.files.map((f) => f.name),
            }));
            return {
                content: [
                    { type: "text" as const, text: JSON.stringify(listing, null, 2) },
                ],
            };
        }
    );

    server.tool(
        "use_skill",
        "Load a superpowers skill by name. Returns the full skill content to follow as instructions. Optional guardrails can enforce required workflow order for the current goal.",
        {
            name: z.string().describe("The skill directory name (e.g. 'brainstorming', 'test-driven-development')"),
            goal: z.string().optional().describe("Current task goal. Used when enforce_guardrails is true."),
            enforce_guardrails: z.boolean().optional().describe("If true, block this skill when required guardrail skills are missing."),
        },
        async ({ name, goal, enforce_guardrails }) => {
            const skill = skillMap.get(name);
            if (!skill) {
                return {
                    content: [
                        {
                            type: "text" as const,
                            text: `Skill '${name}' not found. Use list_skills to see available skills.`,
                        },
                    ],
                    isError: true,
                };
            }

            if (enforce_guardrails) {
                const guardrail = validateNextSkill(
                    goal ?? "",
                    sessionSkillHistory,
                    name,
                    availableSkillNames
                );
                if (!guardrail.valid) {
                    return {
                        content: [
                            {
                                type: "text" as const,
                                text: `Guardrail check failed for intent '${guardrail.intent}'. ${guardrail.violations.join(" ")}`,
                            },
                        ],
                        isError: true,
                    };
                }
            }

            sessionSkillHistory.push(name);
            return {
                content: [{ type: "text" as const, text: skill.content }],
            };
        }
    );

    server.tool(
        "get_skill_file",
        "Load a supporting file from a superpowers skill",
        {
            skill: z.string().describe("The skill directory name"),
            file: z.string().describe("The filename to load (e.g. 'testing-anti-patterns.md')"),
        },
        async ({ skill: skillName, file: fileName }) => {
            const skill = skillMap.get(skillName);
            if (!skill) {
                return {
                    content: [
                        {
                            type: "text" as const,
                            text: `Skill '${skillName}' not found. Use list_skills to see available skills.`,
                        },
                    ],
                    isError: true,
                };
            }

            const fileEntry = skill.files.find((f) => f.name === fileName);
            if (!fileEntry) {
                return {
                    content: [
                        {
                            type: "text" as const,
                            text: `File '${fileName}' not found in skill '${skillName}'. Available files: ${skill.files.map((f) => f.name).join(", ") || "none"}`,
                        },
                    ],
                    isError: true,
                };
            }

            if (!skillsDir) {
                return {
                    content: [
                        {
                            type: "text" as const,
                            text: `Cannot read supporting files: skills directory not available (using bundled skills).`,
                        },
                    ],
                    isError: true,
                };
            }

            try {
                const filePath = join(skillsDir, fileEntry.relativePath);
                const content = await readFile(filePath, "utf-8");
                return {
                    content: [{ type: "text" as const, text: content }],
                };
            } catch {
                return {
                    content: [
                        {
                            type: "text" as const,
                            text: `Error reading file '${fileName}' from skill '${skillName}'.`,
                        },
                    ],
                    isError: true,
                };
            }
        }
    );

    server.tool(
        "recommend_skills",
        "Recommend the most relevant skills for a task using semantic ranking and workflow policy boosts.",
        {
            task: z.string().describe("Task description or user request."),
            repo_context: z.string().optional().describe("Optional project context for better matching."),
            max_results: z.number().int().min(1).max(10).optional().describe("Maximum number of recommendations to return."),
        },
        async ({ task, repo_context, max_results }) => {
            const index = await getIntelligenceIndex();
            const recommendations = recommendSkills(
                index,
                task,
                repo_context,
                max_results ?? 5
            );
            return {
                content: [
                    {
                        type: "text" as const,
                        text: JSON.stringify(recommendations, null, 2),
                    },
                ],
            };
        }
    );

    server.tool(
        "compose_workflow",
        "Compose an ordered multi-skill workflow for a goal using guardrails and semantic relevance.",
        {
            goal: z.string().describe("The user goal to accomplish."),
            max_steps: z.number().int().min(1).max(12).optional().describe("Maximum number of workflow steps."),
        },
        async ({ goal, max_steps }) => {
            const index = await getIntelligenceIndex();
            const workflow = composeWorkflow(index, goal, max_steps ?? 6);
            return {
                content: [
                    {
                        type: "text" as const,
                        text: JSON.stringify(workflow, null, 2),
                    },
                ],
            };
        }
    );

    server.tool(
        "validate_workflow",
        "Validate whether selected skills satisfy required guardrails for a goal.",
        {
            goal: z.string().describe("The goal that determines workflow intent."),
            selected_skills: z.array(z.string()).describe("Skills selected or already used, in order."),
            enforce_order: z.boolean().optional().describe("If true, required skills must come before optional skills."),
        },
        async ({ goal, selected_skills, enforce_order }) => {
            const validation = validateWorkflow(goal, selected_skills, {
                enforceOrder: enforce_order ?? true,
                availableSkillNames,
            });
            return {
                content: [
                    {
                        type: "text" as const,
                        text: JSON.stringify(validation, null, 2),
                    },
                ],
            };
        }
    );

    server.tool(
        "semantic_search_skills",
        "Semantic search across all skill markdown and supporting files.",
        {
            query: z.string().describe("Natural language query to search skill content."),
            skill: z.string().optional().describe("Optional skill directory to restrict the search."),
            max_results: z.number().int().min(1).max(20).optional().describe("Maximum number of matching documents."),
        },
        async ({ query, skill, max_results }) => {
            const index = await getIntelligenceIndex();
            const matches = semanticSearchSkills(index, query, {
                maxResults: max_results ?? 5,
                skillFilter: skill,
            });
            return {
                content: [
                    {
                        type: "text" as const,
                        text: JSON.stringify(matches, null, 2),
                    },
                ],
            };
        }
    );
}
