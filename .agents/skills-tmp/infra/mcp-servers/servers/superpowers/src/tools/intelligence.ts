import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Skill } from "../skills/types.js";

export type WorkflowIntent =
    | "creative"
    | "planning"
    | "implementation"
    | "debugging"
    | "review"
    | "completion"
    | "general";

export interface SkillRecommendation {
    name: string;
    display_name: string;
    description: string;
    score: number;
    reasons: string[];
}

export interface ComposedWorkflowStep {
    skill: string;
    required: boolean;
    score: number;
    reason: string;
}

export interface ComposedWorkflow {
    goal: string;
    intent: WorkflowIntent;
    required_skills: string[];
    steps: ComposedWorkflowStep[];
}

export interface WorkflowValidation {
    valid: boolean;
    intent: WorkflowIntent;
    required_skills: string[];
    missing_required_skills: string[];
    selected_skills: string[];
    violations: string[];
}

export interface NextSkillValidation {
    valid: boolean;
    intent: WorkflowIntent;
    required_skills: string[];
    missing_required_skills: string[];
    violations: string[];
}

export interface SemanticSearchMatch {
    skill: string;
    file: string;
    source: "skill" | "supporting-file";
    uri: string;
    score: number;
    snippet: string;
}

interface IndexedDocument {
    skill: string;
    file: string;
    source: "skill" | "supporting-file";
    uri: string;
    content: string;
    tokens: string[];
    tokenSet: Set<string>;
    embedding: number[];
}

interface IndexedSkill {
    name: string;
    displayName: string;
    description: string;
    tokens: string[];
    tokenSet: Set<string>;
    embedding: number[];
}

export interface SkillIntelligenceIndex {
    documents: IndexedDocument[];
    skills: IndexedSkill[];
}

interface IntentPolicy {
    required: string[];
    recommended: string[];
}

const STOP_WORDS = new Set([
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
    "this",
    "these",
    "those",
    "into",
    "over",
    "under",
    "we",
    "you",
    "your",
    "our",
    "their",
    "before",
    "after",
    "then",
    "than",
    "when",
    "what",
    "which",
    "how",
    "why",
]);

const EMBEDDING_DIMENSION = 256;

const INTENT_POLICIES: Record<WorkflowIntent, IntentPolicy> = {
    creative: {
        required: ["brainstorming"],
        recommended: ["writing-plans"],
    },
    planning: {
        required: ["writing-plans"],
        recommended: ["test-driven-development"],
    },
    implementation: {
        required: ["test-driven-development"],
        recommended: ["verification-before-completion"],
    },
    debugging: {
        required: ["systematic-debugging"],
        recommended: ["test-driven-development"],
    },
    review: {
        required: ["requesting-code-review"],
        recommended: ["receiving-code-review"],
    },
    completion: {
        required: ["verification-before-completion"],
        recommended: ["finishing-a-development-branch"],
    },
    general: {
        required: [],
        recommended: [],
    },
};

function toNumber(value: number, fallback: number): number {
    if (!Number.isFinite(value)) return fallback;
    return value;
}

function clampInteger(value: number | undefined, min: number, max: number, fallback: number): number {
    if (value === undefined) return fallback;
    return Math.max(min, Math.min(max, Math.floor(value)));
}

function fnv1aHash(input: string): number {
    let hash = 0x811c9dc5;
    for (let i = 0; i < input.length; i++) {
        hash ^= input.charCodeAt(i);
        hash +=
            (hash << 1) +
            (hash << 4) +
            (hash << 7) +
            (hash << 8) +
            (hash << 24);
    }
    return hash >>> 0;
}

function tokenize(input: string): string[] {
    return input
        .toLowerCase()
        .replace(/[_/.-]+/g, " ")
        .split(/[^a-z0-9]+/g)
        .map((token) => token.trim())
        .filter((token) => token.length > 1 && !STOP_WORDS.has(token));
}

function createEmbedding(tokens: string[]): number[] {
    const vector = new Array<number>(EMBEDDING_DIMENSION).fill(0);
    const counts = new Map<string, number>();
    for (const token of tokens) {
        counts.set(token, (counts.get(token) ?? 0) + 1);
    }
    for (const [token, count] of counts) {
        const hash = fnv1aHash(token);
        const index = hash % EMBEDDING_DIMENSION;
        const sign = ((hash >>> 8) & 1) === 0 ? 1 : -1;
        vector[index] += sign * (1 + Math.log(count));
    }
    let magnitude = 0;
    for (const value of vector) magnitude += value * value;
    magnitude = Math.sqrt(magnitude);
    if (magnitude === 0) return vector;
    return vector.map((value) => value / magnitude);
}

function cosineSimilarity(a: number[], b: number[]): number {
    if (a.length !== b.length) return 0;
    let dot = 0;
    for (let i = 0; i < a.length; i++) {
        dot += a[i] * b[i];
    }
    return Math.max(0, toNumber(dot, 0));
}

function keywordOverlapScore(queryTokens: string[], targetTokens: Set<string>): number {
    if (queryTokens.length === 0) return 0;
    let overlap = 0;
    for (const token of queryTokens) {
        if (targetTokens.has(token)) overlap++;
    }
    return overlap / queryTokens.length;
}

function intersectingTokens(queryTokens: string[], targetTokens: Set<string>, maxTerms = 4): string[] {
    const seen = new Set<string>();
    const terms: string[] = [];
    for (const token of queryTokens) {
        if (targetTokens.has(token) && !seen.has(token)) {
            terms.push(token);
            seen.add(token);
        }
    }
    terms.sort((a, b) => b.length - a.length);
    return terms.slice(0, maxTerms);
}

function normalizeWhitespace(input: string): string {
    return input.replace(/\s+/g, " ").trim();
}

function createSnippet(content: string, queryTokens: string[]): string {
    const normalized = normalizeWhitespace(content);
    if (normalized.length === 0) return "";
    const lowered = normalized.toLowerCase();
    let pivot = 0;
    for (const token of queryTokens) {
        const index = lowered.indexOf(token);
        if (index >= 0) {
            pivot = index;
            break;
        }
    }
    const start = Math.max(0, pivot - 80);
    const end = Math.min(normalized.length, pivot + 180);
    return normalized.slice(start, end);
}

function dedupeOrdered(values: string[]): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const value of values) {
        if (!seen.has(value)) {
            seen.add(value);
            out.push(value);
        }
    }
    return out;
}

function filterByAvailability(skills: string[], availableSkillNames?: Set<string>): string[] {
    if (!availableSkillNames) return skills;
    return skills.filter((skill) => availableSkillNames.has(skill));
}

function getIntentPolicy(intent: WorkflowIntent, availableSkillNames?: Set<string>): IntentPolicy {
    const policy = INTENT_POLICIES[intent] ?? INTENT_POLICIES.general;
    return {
        required: filterByAvailability(policy.required, availableSkillNames),
        recommended: filterByAvailability(policy.recommended, availableSkillNames),
    };
}

function hasAny(text: string, keywords: string[]): boolean {
    return keywords.some((keyword) => text.includes(keyword));
}

export function inferIntent(input: string): WorkflowIntent {
    const text = input.toLowerCase();
    if (hasAny(text, ["debug", "bug", "flaky", "regression", "incident", "failure", "trace"])) {
        return "debugging";
    }
    if (hasAny(text, ["review", "feedback", "pr ", "pull request", "code review"])) {
        return "review";
    }
    if (hasAny(text, ["complete", "finish", "ship", "release", "done"])) {
        return "completion";
    }
    if (hasAny(text, ["design", "brainstorm", "idea", "approach", "architecture"])) {
        return "creative";
    }
    if (hasAny(text, ["plan", "roadmap", "breakdown", "task list"])) {
        return "planning";
    }
    if (hasAny(text, ["implement", "build", "feature", "refactor", "write tests", "tdd", "code"])) {
        return "implementation";
    }
    return "general";
}

export async function buildSkillIntelligenceIndex(
    skills: Skill[],
    skillsDir?: string
): Promise<SkillIntelligenceIndex> {
    const documents: IndexedDocument[] = [];

    for (const skill of skills) {
        const skillTokens = tokenize(
            `${skill.metadata.name} ${skill.metadata.description} ${skill.content}`
        );
        documents.push({
            skill: skill.directoryName,
            file: "SKILL.md",
            source: "skill",
            uri: `superpowers://skills/${skill.directoryName}/SKILL.md`,
            content: skill.content,
            tokens: skillTokens,
            tokenSet: new Set(skillTokens),
            embedding: createEmbedding(skillTokens),
        });

        for (const file of skill.files) {
            let fileContent = file.name;
            if (skillsDir) {
                try {
                    fileContent = await readFile(join(skillsDir, file.relativePath), "utf-8");
                } catch {
                    fileContent = file.name;
                }
            }
            const fileTokens = tokenize(
                `${skill.metadata.name} ${skill.metadata.description} ${file.name} ${fileContent}`
            );
            documents.push({
                skill: skill.directoryName,
                file: file.name,
                source: "supporting-file",
                uri: `superpowers://skills/${skill.directoryName}/${file.name}`,
                content: fileContent,
                tokens: fileTokens,
                tokenSet: new Set(fileTokens),
                embedding: createEmbedding(fileTokens),
            });
        }
    }

    const skillsIndex: IndexedSkill[] = skills.map((skill) => {
        const skillDocuments = documents.filter((document) => document.skill === skill.directoryName);
        const aggregate = [
            skill.metadata.name,
            skill.metadata.description,
            ...skillDocuments.map((document) => document.content),
        ].join("\n");
        const tokens = tokenize(aggregate);
        return {
            name: skill.directoryName,
            displayName: skill.metadata.name,
            description: skill.metadata.description,
            tokens,
            tokenSet: new Set(tokens),
            embedding: createEmbedding(tokens),
        };
    });

    return {
        documents,
        skills: skillsIndex,
    };
}

export function recommendSkills(
    index: SkillIntelligenceIndex,
    task: string,
    repoContext?: string,
    maxResults = 5
): SkillRecommendation[] {
    const limit = clampInteger(maxResults, 1, 10, 5);
    const fullQuery = normalizeWhitespace(`${task} ${repoContext ?? ""}`);
    const queryTokens = tokenize(fullQuery);
    const queryEmbedding = createEmbedding(queryTokens);
    const intent = inferIntent(fullQuery);
    const policy = getIntentPolicy(intent, new Set(index.skills.map((skill) => skill.name)));

    const recommendations = index.skills
        .map((skill) => {
            const semanticScore = cosineSimilarity(queryEmbedding, skill.embedding);
            const overlapScore = keywordOverlapScore(queryTokens, skill.tokenSet);
            const intentScore = policy.required.includes(skill.name)
                ? 1
                : policy.recommended.includes(skill.name)
                    ? 0.6
                    : 0;
            const score = (semanticScore * 0.65) + (overlapScore * 0.25) + (intentScore * 0.1);

            const reasons: string[] = [];
            if (policy.required.includes(skill.name)) {
                reasons.push(`Required by ${intent} workflow guardrails`);
            } else if (policy.recommended.includes(skill.name)) {
                reasons.push(`Recommended by ${intent} workflow guardrails`);
            }

            const overlaps = intersectingTokens(queryTokens, skill.tokenSet);
            if (overlaps.length > 0) {
                reasons.push(`Keyword overlap: ${overlaps.join(", ")}`);
            }
            if (semanticScore >= 0.2) {
                reasons.push("High semantic similarity to the task");
            }
            if (reasons.length === 0) {
                reasons.push("General relevance to the task");
            }

            return {
                name: skill.name,
                display_name: skill.displayName,
                description: skill.description,
                score: Number(score.toFixed(4)),
                reasons,
            };
        })
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);

    return recommendations;
}

export function composeWorkflow(
    index: SkillIntelligenceIndex,
    goal: string,
    maxSteps = 6
): ComposedWorkflow {
    const limit = clampInteger(maxSteps, 1, 12, 6);
    const availableSkillNames = new Set(index.skills.map((skill) => skill.name));
    const intent = inferIntent(goal);
    const policy = getIntentPolicy(intent, availableSkillNames);
    const recommendations = recommendSkills(index, goal, undefined, Math.max(limit, 6));
    const recommendationMap = new Map(recommendations.map((recommendation) => [recommendation.name, recommendation]));

    const orderedSkills = dedupeOrdered([
        ...policy.required,
        ...policy.recommended,
        ...recommendations.map((recommendation) => recommendation.name),
    ]).slice(0, limit);

    const steps: ComposedWorkflowStep[] = orderedSkills.map((skillName) => {
        const recommendation = recommendationMap.get(skillName);
        const required = policy.required.includes(skillName);
        let reason = "Relevant to the current goal";
        if (required) {
            reason = `Required by guardrails for ${intent} work`;
        } else if (policy.recommended.includes(skillName)) {
            reason = `Recommended by guardrails for ${intent} work`;
        } else if (recommendation) {
            reason = recommendation.reasons[0] ?? reason;
        }

        return {
            skill: skillName,
            required,
            score: recommendation?.score ?? 0,
            reason,
        };
    });

    return {
        goal,
        intent,
        required_skills: policy.required,
        steps,
    };
}

export function validateWorkflow(
    goal: string,
    selectedSkills: string[],
    options?: { enforceOrder?: boolean; availableSkillNames?: Set<string> }
): WorkflowValidation {
    const enforceOrder = options?.enforceOrder ?? true;
    const intent = inferIntent(goal);
    const policy = getIntentPolicy(intent, options?.availableSkillNames);
    const selected = dedupeOrdered(selectedSkills);
    const selectedSet = new Set(selected);
    const missing = policy.required.filter((skill) => !selectedSet.has(skill));
    const violations: string[] = [];

    if (missing.length > 0) {
        violations.push(`Missing required skills: ${missing.join(", ")}`);
    }

    if (enforceOrder && policy.required.length > 0) {
        const firstOptionalIndex = selected.findIndex((skill) => !policy.required.includes(skill));
        if (firstOptionalIndex >= 0) {
            const requiredBeforeOptional = new Set(selected.slice(0, firstOptionalIndex));
            const missingBeforeOptional = policy.required.filter((skill) => !requiredBeforeOptional.has(skill));
            if (missingBeforeOptional.length > 0) {
                violations.push(
                    `Required skills must come before optional skills: ${missingBeforeOptional.join(", ")}`
                );
            }
        }

        let lastRequiredIndex = -1;
        for (const requiredSkill of policy.required) {
            const index = selected.indexOf(requiredSkill);
            if (index === -1) continue;
            if (index < lastRequiredIndex) {
                violations.push("Required skills are out of order");
                break;
            }
            lastRequiredIndex = index;
        }
    }

    return {
        valid: violations.length === 0,
        intent,
        required_skills: policy.required,
        missing_required_skills: missing,
        selected_skills: selected,
        violations,
    };
}

export function validateNextSkill(
    goal: string,
    usedSkills: string[],
    nextSkill: string,
    availableSkillNames?: Set<string>
): NextSkillValidation {
    const intent = inferIntent(goal);
    const policy = getIntentPolicy(intent, availableSkillNames);
    if (policy.required.length === 0) {
        return {
            valid: true,
            intent,
            required_skills: [],
            missing_required_skills: [],
            violations: [],
        };
    }

    const used = new Set(usedSkills);
    const required = policy.required;
    const missing = required.filter((skill) => !used.has(skill));
    const violations: string[] = [];

    if (required.includes(nextSkill)) {
        const requiredIndex = required.indexOf(nextSkill);
        const missingBefore = required
            .slice(0, requiredIndex)
            .filter((skill) => !used.has(skill));
        if (missingBefore.length > 0) {
            violations.push(`Required skills must be used first: ${missingBefore.join(", ")}`);
        }
    } else if (missing.length > 0) {
        violations.push(`Required skills must be used first: ${missing.join(", ")}`);
    }

    return {
        valid: violations.length === 0,
        intent,
        required_skills: required,
        missing_required_skills: missing,
        violations,
    };
}

export function semanticSearchSkills(
    index: SkillIntelligenceIndex,
    query: string,
    options?: { maxResults?: number; skillFilter?: string }
): SemanticSearchMatch[] {
    const limit = clampInteger(options?.maxResults, 1, 20, 5);
    const queryTokens = tokenize(query);
    const queryEmbedding = createEmbedding(queryTokens);
    const filteredDocuments = options?.skillFilter
        ? index.documents.filter((document) => document.skill === options.skillFilter)
        : index.documents;

    const matches = filteredDocuments
        .map((document) => {
            const semanticScore = cosineSimilarity(queryEmbedding, document.embedding);
            const overlapScore = keywordOverlapScore(queryTokens, document.tokenSet);
            const score = (semanticScore * 0.7) + (overlapScore * 0.3);
            return {
                skill: document.skill,
                file: document.file,
                source: document.source,
                uri: document.uri,
                score: Number(score.toFixed(4)),
                snippet: createSnippet(document.content, queryTokens),
            };
        })
        .filter((match) => match.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);

    return matches;
}
