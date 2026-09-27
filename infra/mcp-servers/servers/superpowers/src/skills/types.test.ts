import { describe, it, expect } from "vitest";
import type { Skill, SkillFile, SkillMetadata } from "./types.js";

describe("Skill types", () => {
    it("should allow constructing a valid Skill object", () => {
        const metadata: SkillMetadata = {
            name: "test-driven-development",
            description: "Use when implementing any feature or bugfix",
        };

        const file: SkillFile = {
            name: "testing-anti-patterns.md",
            relativePath: "test-driven-development/testing-anti-patterns.md",
        };

        const skill: Skill = {
            metadata,
            directoryName: "test-driven-development",
            content: "# Test Driven Development\n\nContent here...",
            files: [file],
        };

        expect(skill.metadata.name).toBe("test-driven-development");
        expect(skill.files).toHaveLength(1);
        expect(skill.directoryName).toBe("test-driven-development");
    });
});
