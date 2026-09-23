import { getSkillsDir, getLastUpdateCheck, setLastUpdateCheck } from "./config.js";
import { checkForUpdates, gitPull } from "./git.js";
import { existsSync } from "node:fs";

export async function checkAndApplyUpdates(): Promise<void> {
    const skillsDir = getSkillsDir();
    if (skillsDir && existsSync(skillsDir)) {
        const lastCheck = getLastUpdateCheck() || 0;
        const now = Date.now();
        const ONE_DAY = 24 * 60 * 60 * 1000;

        if (now - lastCheck > ONE_DAY) {
            console.error("Checking for updates...");
            try {
                const hasUpdate = await checkForUpdates(skillsDir);
                if (hasUpdate) {
                    console.error("Update available! Pulling latest changes...");
                    await gitPull(skillsDir);
                    console.error("Updated to latest version.");
                }
                setLastUpdateCheck(now);
            } catch (e) {
                console.error("Failed to check/update:", e);
            }
        }
    }
}
