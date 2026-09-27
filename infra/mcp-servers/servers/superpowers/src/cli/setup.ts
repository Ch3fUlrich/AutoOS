import prompts from "prompts";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import {
    setConfig,
    getSkillsDir,
    setSkillsDir,
    setLastUpdateCheck,
} from "../config.js";
import { gitClone, SUPERPOWERS_REPO } from "../git.js";

export async function runSetup(): Promise<void> {
    let skillsDir = getSkillsDir();

    if (skillsDir && existsSync(skillsDir)) {
        return;
    }

    console.log("Welcome to Superpowers MCP Server Setup!");

    const response = await prompts([
        {
            type: "select",
            name: "installType",
            message: "How would you like to install Superpowers skills?",
            choices: [
                {
                    title: "Download new copy (Recommended)",
                    description: "Clone the repository to a local directory managed by this server",
                    value: "download",
                },
                {
                    title: "Use existing installation",
                    description: "Point to an existing local clone of the superpowers repository",
                    value: "existing",
                },
            ],
        },
    ]);

    if (response.installType === "existing") {
        const pathResponse = await prompts({
            type: "text",
            name: "path",
            message: "Enter the absolute path to your superpowers repository:",
            validate: (value) =>
                existsSync(value) ? true : "Path does not exist",
        });
        skillsDir = pathResponse.path;
    } else if (response.installType === "download") {
        const defaultDir = join(homedir(), ".superpowers-mcp", "skills");

        const confirmDir = await prompts({
            type: "text",
            name: "path",
            message: "Where should we download the skills?",
            initial: defaultDir,
        });

        skillsDir = confirmDir.path;

        if (!skillsDir) {
            console.error("No path provided.");
            return;
        }

        if (existsSync(skillsDir)) {
            const { isGitRepo, gitPull } = await import("../git.js");
            const isRepo = await isGitRepo(skillsDir);

            if (isRepo) {
                console.log(`Directory ${skillsDir} exists and is a git repository.`);
                console.log("Checking for updates...");
                try {
                    await gitPull(skillsDir);
                    console.log("Updated to latest version.");
                    setLastUpdateCheck(Date.now());
                } catch (error) {
                    console.error("Failed to update repository:", error);
                }
            } else {
                console.log(`Directory ${skillsDir} already exists but is not a git repository.`);

                const response = await prompts({
                    type: "confirm",
                    name: "overwrite",
                    message: "Do you want to clear this directory and install fresh?",
                    initial: false,
                });

                if (response.overwrite) {
                    const { rmSync } = await import("node:fs");
                    const { dirname } = await import("node:path");
                    try {
                        rmSync(skillsDir, { recursive: true, force: true });
                        mkdirSync(dirname(skillsDir), { recursive: true });
                        await gitClone(SUPERPOWERS_REPO, skillsDir);
                        console.log("Download complete!");
                        setLastUpdateCheck(Date.now());
                    } catch (error) {
                        console.error("Failed to clear and install:", error);
                        return;
                    }
                } else {
                    console.log("Keeping existing directory contents.");
                }
            }
        } else {
            console.log(`Cloning ${SUPERPOWERS_REPO} to ${skillsDir}...`);
            try {
                mkdirSync(skillsDir, { recursive: true });
                await gitClone(SUPERPOWERS_REPO, skillsDir);
                console.log("Download complete!");
                setLastUpdateCheck(Date.now());
            } catch (error) {
                console.error("Failed to download skills:", error);
                return;
            }
        }
    }

    if (skillsDir) {
        setSkillsDir(skillsDir);
        setConfig({ useLocalSkills: true });
        console.log(`Configuration saved. Skills directory: ${skillsDir}`);
    }
}
