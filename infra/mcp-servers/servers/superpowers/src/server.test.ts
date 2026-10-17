import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createSuperpowersServer } from "./server.js";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("createSuperpowersServer", () => {
    let testDir: string;

    beforeEach(() => {
        testDir = join(tmpdir(), `superpowers-mcp-server-test-${Date.now()}`);
        mkdirSync(testDir, { recursive: true });

        const skillDir = join(testDir, "brainstorming");
        mkdirSync(skillDir);
        writeFileSync(
            join(skillDir, "SKILL.md"),
            `---
name: brainstorming
description: Explore ideas
---

# Brainstorming

Content.`
        );
    });

    afterEach(() => {
        rmSync(testDir, { recursive: true, force: true });
    });

    it("should create a server with skills from a directory", async () => {
        const { server, skills } = await createSuperpowersServer({
            skillsDir: testDir,
        });
        expect(server).toBeDefined();
        expect(skills).toHaveLength(1);
        expect(skills[0].metadata.name).toBe("brainstorming");
    });

    it("should handle empty skills directory", async () => {
        const emptyDir = join(tmpdir(), `empty-test-${Date.now()}`);
        mkdirSync(emptyDir, { recursive: true });

        const { server, skills } = await createSuperpowersServer({
            skillsDir: emptyDir,
        });
        expect(server).toBeDefined();
        expect(skills).toHaveLength(0);

        rmSync(emptyDir, { recursive: true, force: true });
    });
});
