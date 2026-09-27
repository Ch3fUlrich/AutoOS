import { describe, it, expect, vi, beforeEach } from "vitest";
import { gitClone, gitPull, gitFetch, checkForUpdates, isGitRepo } from "./git.js";
import { execFile } from "node:child_process";

vi.mock("node:child_process", () => ({
    execFile: vi.fn(),
}));

function mockExecFile(impl: (file: string, args: string[]) => { stdout: string; stderr: string }) {
    (execFile as any).mockImplementation(
        (file: string, args: string[], cb: (error: Error | null, result: { stdout: string; stderr: string }) => void) => {
            try {
                const result = impl(file, args);
                cb(null, result);
            } catch (e: any) {
                cb(e, { stdout: "", stderr: "" });
            }
        }
    );
}

function mockExecFileError(error: Error) {
    (execFile as any).mockImplementation(
        (_file: string, _args: string[], cb: (error: Error | null, result: { stdout: string; stderr: string }) => void) => {
            cb(error, { stdout: "", stderr: "" });
        }
    );
}

describe("git", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("should git clone using execFile with separate args", async () => {
        mockExecFile(() => ({ stdout: "", stderr: "" }));
        await gitClone("http://repo.git", "/tmp/dir");
        expect(execFile).toHaveBeenCalledWith(
            "git",
            ["clone", "http://repo.git", "/tmp/dir"],
            expect.any(Function)
        );
    });

    it("should handle git clone existing dir error", async () => {
        mockExecFileError(new Error("already exists and is not an empty directory"));
        await expect(gitClone("http://repo.git", "/tmp/dir")).rejects.toThrow(
            "Target directory /tmp/dir already exists and is not empty"
        );
    });

    it("should git pull using execFile with separate args", async () => {
        mockExecFile(() => ({ stdout: "", stderr: "" }));
        await gitPull("/tmp/dir");
        expect(execFile).toHaveBeenCalledWith(
            "git",
            ["-C", "/tmp/dir", "pull"],
            expect.any(Function)
        );
    });

    it("should git fetch using execFile", async () => {
        mockExecFile(() => ({ stdout: "", stderr: "" }));
        await gitFetch("/tmp/dir");
        expect(execFile).toHaveBeenCalledWith(
            "git",
            ["-C", "/tmp/dir", "fetch"],
            expect.any(Function)
        );
    });

    it("should check for updates (true when hashes differ)", async () => {
        mockExecFile((_file, args) => {
            if (args.includes("fetch")) return { stdout: "", stderr: "" };
            if (args.includes("@{u}")) return { stdout: "hash2\n", stderr: "" };
            if (args.includes("@")) return { stdout: "hash1\n", stderr: "" };
            throw new Error("unknown command");
        });
        const hasUpdate = await checkForUpdates("/tmp/dir");
        expect(hasUpdate).toBe(true);
    });

    it("should check for updates (false when hashes match)", async () => {
        mockExecFile((_file, args) => {
            if (args.includes("fetch")) return { stdout: "", stderr: "" };
            if (args.includes("rev-parse")) return { stdout: "hash1\n", stderr: "" };
            throw new Error("unknown command");
        });
        const hasUpdate = await checkForUpdates("/tmp/dir");
        expect(hasUpdate).toBe(false);
    });

    it("should check isGitRepo (true)", async () => {
        mockExecFile(() => ({ stdout: "true", stderr: "" }));
        expect(await isGitRepo("/tmp/dir")).toBe(true);
    });

    it("should check isGitRepo (false)", async () => {
        mockExecFileError(new Error("not a git repo"));
        expect(await isGitRepo("/tmp/dir")).toBe(false);
    });

    it("should not be vulnerable to shell injection", async () => {
        mockExecFile(() => ({ stdout: "", stderr: "" }));
        await gitPull("/tmp/dir; rm -rf /");
        // execFile passes args as array, not interpolated into shell string
        expect(execFile).toHaveBeenCalledWith(
            "git",
            ["-C", "/tmp/dir; rm -rf /", "pull"],
            expect.any(Function)
        );
    });
});
