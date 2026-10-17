import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createSuperpowersServer } from "./server.js";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("superpowers-mcp integration", () => {
    let client: Client;
    let testDir: string;

    beforeAll(async () => {
        testDir = join(tmpdir(), `superpowers-mcp-integration-${Date.now()}`);
        mkdirSync(testDir, { recursive: true });

        // Create test skills
        const brainstormDir = join(testDir, "brainstorming");
        mkdirSync(brainstormDir);
        writeFileSync(
            join(brainstormDir, "SKILL.md"),
            `---
name: brainstorming
description: Explores user intent and design
---

# Brainstorming

Full brainstorming content here.`
        );

        const tddDir = join(testDir, "test-driven-development");
        mkdirSync(tddDir);
        writeFileSync(
            join(tddDir, "SKILL.md"),
            `---
name: test-driven-development
description: TDD workflow
---

# TDD

Test driven development content.`
        );
        writeFileSync(
            join(tddDir, "anti-patterns.md"),
            "# Anti-Patterns\n\nDon't do this."
        );

        const debuggingDir = join(testDir, "systematic-debugging");
        mkdirSync(debuggingDir);
        writeFileSync(
            join(debuggingDir, "SKILL.md"),
            `---
name: systematic-debugging
description: Root cause analysis workflow
---

# Systematic Debugging

Find root cause before patching.`
        );
        writeFileSync(
            join(debuggingDir, "root-cause-tracing.md"),
            "# Root Cause Tracing\n\nTrace symptoms back to the underlying failure source."
        );

        const verificationDir = join(testDir, "verification-before-completion");
        mkdirSync(verificationDir);
        writeFileSync(
            join(verificationDir, "SKILL.md"),
            `---
name: verification-before-completion
description: Verify outputs before completion
---

# Verification

Run tests and validate outputs before claiming success.`
        );

        const { server } = await createSuperpowersServer({ skillsDir: testDir });

        // Connect client to server via in-memory transport
        const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
        client = new Client({ name: "test-client", version: "0.0.1" }, { capabilities: {} });

        await server.connect(serverTransport);
        await client.connect(clientTransport);
    });

    afterAll(() => {
        rmSync(testDir, { recursive: true, force: true });
    });

    it("should list tools", async () => {
        const result = await client.listTools();
        const toolNames = result.tools.map((t) => t.name);
        expect(toolNames).toContain("list_skills");
        expect(toolNames).toContain("use_skill");
        expect(toolNames).toContain("get_skill_file");
        expect(toolNames).toContain("recommend_skills");
        expect(toolNames).toContain("compose_workflow");
        expect(toolNames).toContain("validate_workflow");
        expect(toolNames).toContain("semantic_search_skills");
    });

    it("should list prompts", async () => {
        const result = await client.listPrompts();
        const promptNames = result.prompts.map((p) => p.name);
        expect(promptNames).toContain("superpowers:brainstorming");
        expect(promptNames).toContain("superpowers:test-driven-development");
    });

    it("should list resources", async () => {
        const result = await client.listResources();
        const uris = result.resources.map((r) => r.uri);
        expect(uris).toContain(
            "superpowers://skills/brainstorming/SKILL.md"
        );
        expect(uris).toContain(
            "superpowers://skills/test-driven-development/SKILL.md"
        );
        expect(uris).toContain(
            "superpowers://skills/test-driven-development/anti-patterns.md"
        );
    });

    it("should call list_skills tool", async () => {
        const result = await client.callTool({ name: "list_skills", arguments: {} });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const skills = JSON.parse(text);
        expect(skills).toHaveLength(4);
        expect(skills[0].name).toBe("brainstorming");
    });

    it("should call use_skill tool", async () => {
        const result = await client.callTool({
            name: "use_skill",
            arguments: { name: "brainstorming" },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("# Brainstorming");
    });

    it("should return error for unknown skill", async () => {
        const result = await client.callTool({
            name: "use_skill",
            arguments: { name: "nonexistent" },
        });
        expect(result.isError).toBe(true);
    });

    it("should get a prompt", async () => {
        const result = await client.getPrompt({
            name: "superpowers:brainstorming",
        });
        expect(result.messages).toHaveLength(1);
        expect(result.messages[0].role).toBe("user");
        const content = result.messages[0].content as { type: string; text: string };
        expect(content.text).toContain("# Brainstorming");
    });

    it("should read a resource", async () => {
        const result = await client.readResource({
            uri: "superpowers://skills/brainstorming/SKILL.md",
        });
        expect(result.contents).toHaveLength(1);
        expect(result.contents[0].text).toContain("# Brainstorming");
    });

    it("should call get_skill_file tool", async () => {
        const result = await client.callTool({
            name: "get_skill_file",
            arguments: {
                skill: "test-driven-development",
                file: "anti-patterns.md",
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        expect(text).toContain("# Anti-Patterns");
    });

    it("should call recommend_skills tool", async () => {
        const result = await client.callTool({
            name: "recommend_skills",
            arguments: {
                task: "Implement a feature with tests first and verify output",
                max_results: 3,
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const recommendations = JSON.parse(text);
        expect(recommendations).toHaveLength(3);
        const recommendedNames = recommendations.map((recommendation: { name: string }) => recommendation.name);
        expect(recommendedNames).toContain("test-driven-development");
    });

    it("should call compose_workflow tool", async () => {
        const result = await client.callTool({
            name: "compose_workflow",
            arguments: {
                goal: "Debug a flaky production issue",
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const workflow = JSON.parse(text);
        expect(workflow.intent).toBe("debugging");
        expect(workflow.steps[0].skill).toBe("systematic-debugging");
    });

    it("should call validate_workflow tool", async () => {
        const result = await client.callTool({
            name: "validate_workflow",
            arguments: {
                goal: "Debug a flaky production issue",
                selected_skills: ["verification-before-completion"],
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const validation = JSON.parse(text);
        expect(validation.valid).toBe(false);
        expect(validation.missing_required_skills).toContain("systematic-debugging");
    });

    it("should call semantic_search_skills tool across supporting files", async () => {
        const result = await client.callTool({
            name: "semantic_search_skills",
            arguments: {
                query: "trace symptoms to underlying failure source",
                max_results: 2,
            },
        });
        const text = (result.content as Array<{ type: string; text: string }>)[0].text;
        const matches = JSON.parse(text);
        expect(matches.length).toBeGreaterThan(0);
        expect(matches[0].skill).toBe("systematic-debugging");
        expect(matches[0].file).toBe("root-cause-tracing.md");
    });
});
