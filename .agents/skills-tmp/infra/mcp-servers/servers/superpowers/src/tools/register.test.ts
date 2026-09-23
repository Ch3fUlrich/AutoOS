import { describe, it, expect, beforeAll } from "vitest";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { registerTools } from "./register.js";
import type { Skill } from "../skills/types.js";

function makeTestSkills(): Skill[] {
    return [
        {
            metadata: { name: "brainstorming", description: "Explore ideas" },
            directoryName: "brainstorming",
            content: "# Brainstorming\n\nExplore user intent and design approaches before coding.",
            files: [],
        },
        {
            metadata: { name: "writing plans", description: "Plan implementation tasks" },
            directoryName: "writing-plans",
            content: "# Writing Plans\n\nCreate a step-by-step implementation plan before coding.",
            files: [],
        },
        {
            metadata: { name: "tdd", description: "Test driven development" },
            directoryName: "test-driven-development",
            content: "# TDD\n\nWrite a failing test first. Red green refactor cycle.",
            files: [
                {
                    name: "anti-patterns.md",
                    relativePath: "test-driven-development/anti-patterns.md",
                },
            ],
        },
        {
            metadata: { name: "systematic debugging", description: "Root cause debugging workflow" },
            directoryName: "systematic-debugging",
            content: "# Systematic Debugging\n\nInvestigate root cause before fixing bugs.",
            files: [],
        },
        {
            metadata: { name: "verification", description: "Verify before completion" },
            directoryName: "verification-before-completion",
            content: "# Verification\n\nRun tests and verify command output before claiming success.",
            files: [],
        },
    ];
}

describe("registerTools", () => {
    let client: Client;

    beforeAll(async () => {
        const server = new McpServer({ name: "test", version: "0.0.1" });
        const skills = makeTestSkills();
        registerTools(server, skills);

        const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
        client = new Client({ name: "test-client", version: "0.0.1" }, { capabilities: {} });
        await server.connect(serverTransport);
        await client.connect(clientTransport);
    });

    it("should register all core and intelligence tools", async () => {
        const result = await client.listTools();
        const toolNames = result.tools.map((t) => t.name);
        expect(toolNames).toContain("list_skills");
        expect(toolNames).toContain("use_skill");
        expect(toolNames).toContain("get_skill_file");
        expect(toolNames).toContain("recommend_skills");
        expect(toolNames).toContain("compose_workflow");
        expect(toolNames).toContain("validate_workflow");
        expect(toolNames).toContain("semantic_search_skills");
        expect(toolNames).toHaveLength(7);
    });

    it("list_skills should return all skills with metadata", async () => {
        const result = await client.callTool({ name: "list_skills", arguments: {} });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const skills = JSON.parse(text);
        expect(skills).toHaveLength(5);
        expect(skills[0].name).toBe("brainstorming");
        expect(skills[0].description).toBe("Explore ideas");
        expect(skills[0].files).toEqual([]);
        expect(skills[2].name).toBe("test-driven-development");
        expect(skills[2].files).toEqual(["anti-patterns.md"]);
    });

    it("use_skill should return full skill content", async () => {
        const result = await client.callTool({
            name: "use_skill",
            arguments: { name: "brainstorming" },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toBe("# Brainstorming\n\nExplore user intent and design approaches before coding.");
        expect(result.isError).toBeFalsy();
    });

    it("use_skill should return error for unknown skill", async () => {
        const result = await client.callTool({
            name: "use_skill",
            arguments: { name: "nonexistent" },
        });
        expect(result.isError).toBe(true);
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("not found");
        expect(text).toContain("list_skills");
    });

    it("get_skill_file should return error for unknown skill", async () => {
        const result = await client.callTool({
            name: "get_skill_file",
            arguments: { skill: "nonexistent", file: "foo.md" },
        });
        expect(result.isError).toBe(true);
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("not found");
    });

    it("get_skill_file should return error for unknown file in valid skill", async () => {
        const result = await client.callTool({
            name: "get_skill_file",
            arguments: { skill: "brainstorming", file: "nonexistent.md" },
        });
        expect(result.isError).toBe(true);
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("not found");
        expect(text).toContain("brainstorming");
    });

    it("get_skill_file should list available files when file not found", async () => {
        const result = await client.callTool({
            name: "get_skill_file",
            arguments: { skill: "test-driven-development", file: "wrong.md" },
        });
        expect(result.isError).toBe(true);
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("anti-patterns.md");
    });

    it("get_skill_file should return error when no skillsDir provided", async () => {
        const result = await client.callTool({
            name: "get_skill_file",
            arguments: { skill: "test-driven-development", file: "anti-patterns.md" },
        });
        expect(result.isError).toBe(true);
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("skills directory not available");
    });

    it("recommend_skills should rank relevant skills for a task", async () => {
        const result = await client.callTool({
            name: "recommend_skills",
            arguments: {
                task: "Implement a new API and write tests first using red green refactor",
                repo_context: "TypeScript MCP server",
                max_results: 3,
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const recommendations = JSON.parse(text);
        expect(recommendations).toHaveLength(3);
        expect(recommendations[0].name).toBe("test-driven-development");
        expect(recommendations[0].score).toBeGreaterThan(0);
        expect(Array.isArray(recommendations[0].reasons)).toBe(true);
    });

    it("compose_workflow should build an ordered workflow for a goal", async () => {
        const result = await client.callTool({
            name: "compose_workflow",
            arguments: {
                goal: "Design a caching strategy for API requests",
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const workflow = JSON.parse(text);
        expect(workflow.intent).toBe("creative");
        expect(workflow.steps.length).toBeGreaterThan(0);
        expect(workflow.steps[0].skill).toBe("brainstorming");
        expect(typeof workflow.steps[0].reason).toBe("string");
    });

    it("validate_workflow should report missing required skills", async () => {
        const result = await client.callTool({
            name: "validate_workflow",
            arguments: {
                goal: "Implement a new auth flow",
                selected_skills: ["verification-before-completion"],
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const validation = JSON.parse(text);
        expect(validation.valid).toBe(false);
        expect(validation.missing_required_skills).toContain("test-driven-development");
        expect(validation.violations.length).toBeGreaterThan(0);
    });

    it("use_skill should enforce guardrails when requested", async () => {
        const blocked = await client.callTool({
            name: "use_skill",
            arguments: {
                name: "verification-before-completion",
                goal: "Implement a new auth flow",
                enforce_guardrails: true,
            },
        });
        expect(blocked.isError).toBe(true);
        const blockedText = (blocked.content as Array<{ type: string; text: string }>)[0].text;
        expect(blockedText).toContain("Required skills must be used first");
        expect(blockedText).toContain("test-driven-development");

        const allowedRequired = await client.callTool({
            name: "use_skill",
            arguments: {
                name: "test-driven-development",
                goal: "Implement a new auth flow",
                enforce_guardrails: true,
            },
        });
        expect(allowedRequired.isError).toBeFalsy();

        const allowedFollowUp = await client.callTool({
            name: "use_skill",
            arguments: {
                name: "verification-before-completion",
                goal: "Implement a new auth flow",
                enforce_guardrails: true,
            },
        });
        expect(allowedFollowUp.isError).toBeFalsy();
    });

    it("semantic_search_skills should find semantically relevant skill content", async () => {
        const result = await client.callTool({
            name: "semantic_search_skills",
            arguments: {
                query: "debug production failures with root cause analysis",
                max_results: 2,
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const matches = JSON.parse(text);
        expect(matches.length).toBeGreaterThan(0);
        expect(matches[0].skill).toBe("systematic-debugging");
        expect(matches[0].score).toBeGreaterThan(0);
        expect(matches[0].uri).toContain("superpowers://skills/systematic-debugging/SKILL.md");
    });
});
