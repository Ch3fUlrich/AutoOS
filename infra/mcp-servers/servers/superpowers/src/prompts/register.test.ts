import { describe, it, expect, beforeAll } from "vitest";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { registerPrompts } from "./register.js";
import type { Skill } from "../skills/types.js";

describe("registerPrompts", () => {
    let client: Client;

    beforeAll(async () => {
        const server = new McpServer({ name: "test", version: "0.0.1" });
        const skills: Skill[] = [
            {
                metadata: { name: "brainstorming", description: "Explore ideas" },
                directoryName: "brainstorming",
                content: "# Brainstorming\n\nContent.",
                files: [],
            },
            {
                metadata: { name: "tdd", description: "TDD workflow" },
                directoryName: "test-driven-development",
                content: "# TDD\n\nTDD content.",
                files: [],
            },
        ];
        registerPrompts(server, skills);

        const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
        client = new Client({ name: "test-client", version: "0.0.1" }, { capabilities: {} });
        await server.connect(serverTransport);
        await client.connect(clientTransport);
    });

    it("should register a prompt for each skill", async () => {
        const result = await client.listPrompts();
        const promptNames = result.prompts.map((p) => p.name);
        expect(promptNames).toContain("superpowers:brainstorming");
        expect(promptNames).toContain("superpowers:test-driven-development");
        expect(promptNames).toHaveLength(2);
    });

    it("should include skill description in prompt metadata", async () => {
        const result = await client.listPrompts();
        const brainstorm = result.prompts.find((p) => p.name === "superpowers:brainstorming");
        expect(brainstorm?.description).toBe("Explore ideas");
    });

    it("should return full SKILL.md content as user message", async () => {
        const result = await client.getPrompt({ name: "superpowers:brainstorming" });
        expect(result.messages).toHaveLength(1);
        expect(result.messages[0].role).toBe("user");
        const content = result.messages[0].content as { type: string; text: string };
        expect(content.type).toBe("text");
        expect(content.text).toBe("# Brainstorming\n\nContent.");
    });

    it("should return correct content for different skills", async () => {
        const result = await client.getPrompt({ name: "superpowers:test-driven-development" });
        const content = result.messages[0].content as { type: string; text: string };
        expect(content.text).toBe("# TDD\n\nTDD content.");
    });
});
