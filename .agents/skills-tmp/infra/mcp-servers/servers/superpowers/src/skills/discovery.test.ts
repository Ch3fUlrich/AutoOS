import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
    parseSkillContent,
    discoverSkillsFromDirectory,
    resolveSkillsDirectory,
} from "./discovery.js";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("parseSkillContent", () => {
    it("should extract metadata from YAML frontmatter", () => {
        const content = `---
name: brainstorming
description: Explores user intent, requirements and design before implementation
---

# Brainstorming Ideas Into Designs

Content here...`;

        const result = parseSkillContent(content);
        expect(result.name).toBe("brainstorming");
        expect(result.description).toBe(
            "Explores user intent, requirements and design before implementation"
        );
        expect(result.body).toContain("# Brainstorming Ideas Into Designs");
    });

    it("should handle missing frontmatter gracefully", () => {
        const content = "# No Frontmatter\n\nJust content.";
        const result = parseSkillContent(content);
        expect(result.name).toBe("");
        expect(result.description).toBe("");
        expect(result.body).toContain("# No Frontmatter");
    });

    it("should handle partial frontmatter", () => {
        const content = `---
name: partial-skill
---

# Partial`;

        const result = parseSkillContent(content);
        expect(result.name).toBe("partial-skill");
        expect(result.description).toBe("");
    });
});

describe("discoverSkillsFromDirectory", () => {
    let testDir: string;

    beforeEach(() => {
        testDir = join(tmpdir(), `superpowers-mcp-test-${Date.now()}`);
        mkdirSync(testDir, { recursive: true });
    });

    afterEach(() => {
        rmSync(testDir, { recursive: true, force: true });
    });

    it("should discover skills from a directory", async () => {
        // Create a mock skill
        const skillDir = join(testDir, "brainstorming");
        mkdirSync(skillDir);
        writeFileSync(
            join(skillDir, "SKILL.md"),
            `---
name: brainstorming
description: Explores user intent
---

# Brainstorming

Content here.`
        );

        const skills = await discoverSkillsFromDirectory(testDir);
        expect(skills).toHaveLength(1);
        expect(skills[0].metadata.name).toBe("brainstorming");
        expect(skills[0].directoryName).toBe("brainstorming");
        expect(skills[0].content).toContain("# Brainstorming");
        expect(skills[0].files).toHaveLength(0);
    });

    it("should discover supporting files", async () => {
        const skillDir = join(testDir, "tdd");
        mkdirSync(skillDir);
        writeFileSync(
            join(skillDir, "SKILL.md"),
            `---
name: test-driven-development
description: Use when implementing features
---

# TDD`
        );
        writeFileSync(
            join(skillDir, "testing-anti-patterns.md"),
            "# Anti-Patterns\n\nContent."
        );

        const skills = await discoverSkillsFromDirectory(testDir);
        expect(skills).toHaveLength(1);
        expect(skills[0].files).toHaveLength(1);
        expect(skills[0].files[0].name).toBe("testing-anti-patterns.md");
        expect(skills[0].files[0].relativePath).toBe(
            "tdd/testing-anti-patterns.md"
        );
    });

    it("should skip directories without SKILL.md", async () => {
        const emptyDir = join(testDir, "not-a-skill");
        mkdirSync(emptyDir);
        writeFileSync(join(emptyDir, "random.txt"), "not a skill");

        const skills = await discoverSkillsFromDirectory(testDir);
        expect(skills).toHaveLength(0);
    });

    it("should handle non-existent directory", async () => {
        const skills = await discoverSkillsFromDirectory("/nonexistent/path");
        expect(skills).toHaveLength(0);
    });
});

describe("resolveSkillsDirectory", () => {
    let testDir: string;

    beforeEach(() => {
        testDir = join(tmpdir(), `superpowers-mcp-test-${Date.now()}`);
        mkdirSync(testDir, { recursive: true });
    });

    afterEach(() => {
        rmSync(testDir, { recursive: true, force: true });
    });

    it("should use SUPERPOWERS_SKILLS_DIR env var when set", () => {
        const result = resolveSkillsDirectory({ SUPERPOWERS_SKILLS_DIR: testDir });
        expect(result).toBe(testDir);
    });

    it("should return null when env var points to nonexistent dir", () => {
        const result = resolveSkillsDirectory({
            SUPERPOWERS_SKILLS_DIR: "/nonexistent",
        });
        expect(result).toBeNull();
    });

    it("should scan plugin cache when no env var set", () => {
        // Create mock plugin cache structure
        const cacheDir = join(
            testDir,
            ".claude",
            "plugins",
            "cache",
            "claude-plugins-official",
            "superpowers",
            "4.3.0",
            "skills"
        );
        mkdirSync(cacheDir, { recursive: true });

        const result = resolveSkillsDirectory({}, join(testDir, ".claude"));
        expect(result).toBe(cacheDir);
    });

    it("should pick the latest version from plugin cache", () => {
        const base = join(
            testDir,
            ".claude",
            "plugins",
            "cache",
            "claude-plugins-official",
            "superpowers"
        );
        mkdirSync(join(base, "3.0.0", "skills"), { recursive: true });
        mkdirSync(join(base, "4.3.0", "skills"), { recursive: true });
        mkdirSync(join(base, "4.2.0", "skills"), { recursive: true });

        const result = resolveSkillsDirectory({}, join(testDir, ".claude"));
        expect(result).toBe(join(base, "4.3.0", "skills"));
    });
});
