import { describe, it, expect, beforeAll } from "vitest";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { registerResources } from "./register.js";
import type { Skill } from "../skills/types.js";

describe("registerResources", () => {
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
                content: "# TDD\n\nContent.",
                files: [
                    {
                        name: "anti-patterns.md",
                        relativePath: "test-driven-development/anti-patterns.md",
                    },
                ],
            },
        ];
        registerResources(server, skills);

        const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
        client = new Client({ name: "test-client", version: "0.0.1" }, { capabilities: {} });
        await server.connect(serverTransport);
        await client.connect(clientTransport);
    });

    it("should register static resources for each skill SKILL.md", async () => {
        const result = await client.listResources();
        const uris = result.resources.map((r) => r.uri);
        expect(uris).toContain("superpowers://skills/brainstorming/SKILL.md");
        expect(uris).toContain("superpowers://skills/test-driven-development/SKILL.md");
    });

    it("should register static resources for supporting files", async () => {
        const result = await client.listResources();
        const uris = result.resources.map((r) => r.uri);
        expect(uris).toContain("superpowers://skills/test-driven-development/anti-patterns.md");
    });

    it("should return skill content when reading a SKILL.md resource", async () => {
        const result = await client.readResource({
            uri: "superpowers://skills/brainstorming/SKILL.md",
        });
        expect(result.contents).toHaveLength(1);
        expect(result.contents[0].text).toBe("# Brainstorming\n\nContent.");
    });

    it("should set correct mimeType on resources", async () => {
        const result = await client.listResources();
        const brainstorm = result.resources.find(
            (r) => r.uri === "superpowers://skills/brainstorming/SKILL.md"
        );
        expect(brainstorm?.mimeType).toBe("text/markdown");
    });

    it("should include description from skill metadata", async () => {
        const result = await client.listResources();
        const brainstorm = result.resources.find(
            (r) => r.uri === "superpowers://skills/brainstorming/SKILL.md"
        );
        expect(brainstorm?.description).toBe("Explore ideas");
    });

    it("should register a resource template for dynamic access", async () => {
        const result = await client.listResourceTemplates();
        expect(result.resourceTemplates.length).toBeGreaterThanOrEqual(1);
        const template = result.resourceTemplates.find(
            (t) => t.uriTemplate === "superpowers://skills/{skillName}/{fileName}"
        );
        expect(template).toBeDefined();
    });
});
