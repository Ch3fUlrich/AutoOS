import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { runSetup } from "./setup.js";
import prompts from "prompts";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import * as config from "../config.js";
import * as git from "../git.js";

vi.mock("prompts");
vi.mock("node:fs");
vi.mock("node:path");
vi.mock("node:os");
vi.mock("../config.js");
vi.mock("../git.js", async (importOriginal) => {
    const actual = await importOriginal<typeof import("../git.js")>();
    return {
        ...actual,
        gitClone: vi.fn(),
        gitPull: vi.fn(),
        isGitRepo: vi.fn(),
    };
});

describe("runSetup", () => {
    beforeEach(() => {
        vi.clearAllMocks();

        // Default mocks
        vi.mocked(config.getSkillsDir).mockReturnValue(undefined);
        vi.mocked(fs.existsSync).mockReturnValue(false);
        vi.mocked(os.homedir).mockReturnValue("/mock/home");
        vi.mocked(path.join).mockImplementation((...args) => args.join("/"));
        vi.mocked(path.dirname).mockImplementation((p) => {
            const parts = p.split("/");
            parts.pop();
            return parts.join("/") || "/";
        });

        // Supress console logs during tests to keep output clean
        vi.spyOn(console, "log").mockImplementation(() => {});
        vi.spyOn(console, "error").mockImplementation(() => {});
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("should return early if skills directory already exists", async () => {
        vi.mocked(config.getSkillsDir).mockReturnValue("/existing/skills");
        // We only want existsSync to return true for this specific path
        vi.mocked(fs.existsSync).mockImplementation((p) => p === "/existing/skills");

        await runSetup();

        expect(prompts).not.toHaveBeenCalled();
        expect(config.setConfig).not.toHaveBeenCalled();
    });

    it("should prompt for existing path when 'existing' install type is chosen", async () => {
        // First prompt returns 'existing'
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "existing" } as any)
            .mockResolvedValueOnce({ path: "/user/provided/path" } as any);

        await runSetup();

        expect(prompts).toHaveBeenCalledTimes(2);
        expect(config.setSkillsDir).toHaveBeenCalledWith("/user/provided/path");
        expect(config.setConfig).toHaveBeenCalledWith({ useLocalSkills: true });
    });

    it("should download to new directory when 'download' is chosen and dir does not exist", async () => {
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "download" } as any)
            .mockResolvedValueOnce({ path: "/mock/home/.superpowers-mcp/skills" } as any);

        vi.mocked(fs.existsSync).mockReturnValue(false); // dir doesn't exist

        await runSetup();

        expect(fs.mkdirSync).toHaveBeenCalledWith("/mock/home/.superpowers-mcp/skills", { recursive: true });
        expect(git.gitClone).toHaveBeenCalledWith(git.SUPERPOWERS_REPO, "/mock/home/.superpowers-mcp/skills");
        expect(config.setLastUpdateCheck).toHaveBeenCalled();
        expect(config.setSkillsDir).toHaveBeenCalledWith("/mock/home/.superpowers-mcp/skills");
        expect(config.setConfig).toHaveBeenCalledWith({ useLocalSkills: true });
    });

    it("should error and exit if no path is provided during download", async () => {
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "download" } as any)
            .mockResolvedValueOnce({ path: "" } as any); // Empty path

        await runSetup();

        expect(console.error).toHaveBeenCalledWith("No path provided.");
        expect(fs.mkdirSync).not.toHaveBeenCalled();
        expect(git.gitClone).not.toHaveBeenCalled();
        expect(config.setSkillsDir).not.toHaveBeenCalled();
    });

    it("should pull updates if download dir exists and is a git repository", async () => {
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "download" } as any)
            .mockResolvedValueOnce({ path: "/existing/repo" } as any);

        vi.mocked(fs.existsSync).mockImplementation((p) => p === "/existing/repo"); // dir exists
        vi.mocked(git.isGitRepo).mockResolvedValue(true); // is a git repo

        await runSetup();

        expect(git.gitPull).toHaveBeenCalledWith("/existing/repo");
        expect(config.setLastUpdateCheck).toHaveBeenCalled();
        expect(config.setSkillsDir).toHaveBeenCalledWith("/existing/repo");
        expect(config.setConfig).toHaveBeenCalledWith({ useLocalSkills: true });
    });

    it("should clear and clone if download dir exists, is not git repo, and user confirms overwrite", async () => {
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "download" } as any)
            .mockResolvedValueOnce({ path: "/existing/non-repo" } as any)
            .mockResolvedValueOnce({ overwrite: true } as any);

        vi.mocked(fs.existsSync).mockImplementation((p) => p === "/existing/non-repo"); // dir exists
        vi.mocked(git.isGitRepo).mockResolvedValue(false); // not a git repo

        await runSetup();

        expect(fs.rmSync).toHaveBeenCalledWith("/existing/non-repo", { recursive: true, force: true });
        expect(fs.mkdirSync).toHaveBeenCalledWith("/existing", { recursive: true });
        expect(git.gitClone).toHaveBeenCalledWith(git.SUPERPOWERS_REPO, "/existing/non-repo");
        expect(config.setLastUpdateCheck).toHaveBeenCalled();
        expect(config.setSkillsDir).toHaveBeenCalledWith("/existing/non-repo");
        expect(config.setConfig).toHaveBeenCalledWith({ useLocalSkills: true });
    });

    it("should keep existing dir and set config if user declines overwrite", async () => {
        vi.mocked(prompts)
            .mockResolvedValueOnce({ installType: "download" } as any)
            .mockResolvedValueOnce({ path: "/existing/non-repo" } as any)
            .mockResolvedValueOnce({ overwrite: false } as any);

        vi.mocked(fs.existsSync).mockImplementation((p) => p === "/existing/non-repo"); // dir exists
        vi.mocked(git.isGitRepo).mockResolvedValue(false); // not a git repo

        await runSetup();

        expect(fs.rmSync).not.toHaveBeenCalled();
        expect(git.gitClone).not.toHaveBeenCalled();
        expect(config.setSkillsDir).toHaveBeenCalledWith("/existing/non-repo");
        expect(config.setConfig).toHaveBeenCalledWith({ useLocalSkills: true });
    });
});
