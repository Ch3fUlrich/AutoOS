import Conf from "conf";

interface SuperpowersConfig {
    skillsDir?: string;
    lastUpdateCheck?: number;
    useLocalSkills?: boolean;
}

const config = new Conf<SuperpowersConfig>({
    projectName: "superpowers-mcp",
    defaults: {
        useLocalSkills: false,
    },
});

export function getConfig(): SuperpowersConfig {
    return config.store;
}

export function setConfig(newConfig: Partial<SuperpowersConfig>): void {
    config.set(newConfig);
}

export function getSkillsDir(): string | undefined {
    return config.get("skillsDir");
}

export function setSkillsDir(dir: string): void {
    config.set("skillsDir", dir);
}

export function getLastUpdateCheck(): number | undefined {
    return config.get("lastUpdateCheck");
}

export function setLastUpdateCheck(timestamp: number): void {
    config.set("lastUpdateCheck", timestamp);
}
