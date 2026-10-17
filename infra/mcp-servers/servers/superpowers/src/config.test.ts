import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getConfig, setConfig, getSkillsDir, setSkillsDir, getLastUpdateCheck, setLastUpdateCheck } from './config.js';

const { mockStore, mockSet, mockGet } = vi.hoisted(() => {
    const mockStore = new Map();
    const mockSet = vi.fn((key: any, value: any) => {
        if (typeof key === 'object') {
            Object.entries(key).forEach(([k, v]) => mockStore.set(k, v));
        } else {
            mockStore.set(key, value);
        }
    });
    const mockGet = vi.fn((key: any) => mockStore.get(key));
    return { mockStore, mockSet, mockGet };
});

vi.mock('conf', () => {
    return {
        default: class MockConf {
            store = Object.fromEntries(mockStore);
            set = mockSet;
            get = mockGet;
        }
    };
});

describe('config', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockStore.clear();
    });

    it('should set and get skillsDir', () => {
        setSkillsDir('/tmp/skills');
        expect(mockSet).toHaveBeenCalledWith('skillsDir', '/tmp/skills');

        mockStore.set('skillsDir', '/tmp/skills'); // Simulate store update
        expect(getSkillsDir()).toBe('/tmp/skills');
    });

    it('should set and get lastUpdateCheck', () => {
        const now = 1234567890;
        setLastUpdateCheck(now);
        expect(mockSet).toHaveBeenCalledWith('lastUpdateCheck', now);

        mockStore.set('lastUpdateCheck', now);
        expect(getLastUpdateCheck()).toBe(now);
    });

    it('should set partial config', () => {
        setConfig({ useLocalSkills: true });
        expect(mockSet).toHaveBeenCalledWith({ useLocalSkills: true });
    });
});
