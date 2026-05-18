import { describe, it, expect } from 'vitest';

// Helper function extracted from RepositoryMap.jsx
function hasGraphNodes(payload) {
    // A valid graph response exists if it has a nodes array (even if empty).
    // Empty graphs are valid snapshots (e.g., after COPY sync with 0 source models).
    return Array.isArray(payload?.nodes);
}

describe('hasGraphNodes', () => {
    it('should return true for graphs with nodes', () => {
        const graph = { nodes: [{ id: 'node1' }], edges: [] };
        expect(hasGraphNodes(graph)).toBe(true);
    });

    it('should return true for EMPTY graphs (0 models snapshot)', () => {
        const emptyGraph = { nodes: [], edges: [] };
        expect(hasGraphNodes(emptyGraph)).toBe(true);
    });

    it('should return false for null payload', () => {
        expect(hasGraphNodes(null)).toBe(false);
    });

    it('should return false for undefined payload', () => {
        expect(hasGraphNodes(undefined)).toBe(false);
    });

    it('should return false for payload without nodes array', () => {
        const invalidGraph = { edges: [] };
        expect(hasGraphNodes(invalidGraph)).toBe(false);
    });

    it('should return false if nodes is not an array', () => {
        const invalidGraph = { nodes: 'not-an-array' };
        expect(hasGraphNodes(invalidGraph)).toBe(false);
    });
});
