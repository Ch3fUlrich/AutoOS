import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export const SUPERPOWERS_REPO = "https://github.com/obra/superpowers.git";

export async function gitClone(repoUrl: string, targetDir: string): Promise<void> {
    try {
        await execFileAsync("git", ["clone", repoUrl, targetDir]);
    } catch (e: any) {
        if (e.message.includes("already exists and is not an empty directory")) {
            throw new Error(`Target directory ${targetDir} already exists and is not empty`);
        }
        throw e;
    }
}

export async function gitPull(targetDir: string): Promise<void> {
    await execFileAsync("git", ["-C", targetDir, "pull"]);
}

export async function gitFetch(targetDir: string): Promise<void> {
    await execFileAsync("git", ["-C", targetDir, "fetch"]);
}

export async function gitRevParseBase(targetDir: string): Promise<string> {
    const { stdout } = await execFileAsync("git", ["-C", targetDir, "rev-parse", "@"]);
    return stdout.trim();
}

export async function gitRevParseRemote(targetDir: string): Promise<string> {
    const { stdout } = await execFileAsync("git", ["-C", targetDir, "rev-parse", "@{u}"]);
    return stdout.trim();
}

export async function checkForUpdates(targetDir: string): Promise<boolean> {
    try {
        await gitFetch(targetDir);
        const local = await gitRevParseBase(targetDir);
        const remote = await gitRevParseRemote(targetDir);
        return local !== remote;
    } catch (error) {
        console.error("Failed to check for updates:", error);
        return false;
    }
}

export async function isGitRepo(targetDir: string): Promise<boolean> {
    try {
        await execFileAsync("git", ["-C", targetDir, "rev-parse", "--is-inside-work-tree"]);
        return true;
    } catch {
        return false;
    }
}
