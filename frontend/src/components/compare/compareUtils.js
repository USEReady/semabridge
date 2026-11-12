// Helpers for the Compare screen (Screen 10 of the app redesign).

/** Friendly names for the formats the backend's adapters detect. */
export const FORMAT_LABELS = {
    OSI: 'OSI',
    FABRIC_OSI: 'Fabric OSI',
    SML: 'SML',
    TSML: 'TSML',
    SNOWFLAKE: 'Snowflake semantic view',
    SNOWFLAKE_TSML: 'Snowflake (TSML)',
    ATSCALE_SML: 'AtScale SML',
    SEMABRIDGE_SML: 'Semabridge SML',
    DBT: 'dbt MetricFlow',
    CUBE: 'Cube',
    GENERIC: 'Generic YAML',
};

export const formatLabel = (f) => FORMAT_LABELS[String(f || '').toUpperCase()] || (f ? String(f) : 'Unknown');

// AI models that can judge whether two changed measure expressions mean the
// same thing. Model IDs were verified against provider docs (April 2025).
// TODO(settings): this list belongs in Settings › AI providers with one
// workspace-wide default; it lives behind "Advanced" here until that exists.
export const LLM_PROVIDERS = [
    { provider: 'google', model: 'gemini-2.0-flash', label: 'Google — Gemini 2.0 Flash' },
    { provider: 'openai', model: 'gpt-4.1', label: 'OpenAI — GPT-4.1' },
    { provider: 'openai', model: 'gpt-4.1-mini', label: 'OpenAI — GPT-4.1 Mini' },
    { provider: 'anthropic', model: 'claude-3-5-sonnet-20241022', label: 'Anthropic — Claude 3.5 Sonnet' },
    { provider: 'anthropic', model: 'claude-3-5-haiku-20241022', label: 'Anthropic — Claude 3.5 Haiku' },
    { provider: 'groq', model: 'meta-llama/llama-4-scout-17b-16e-instruct', label: 'Groq — Llama 4 Scout 17B' },
    { provider: 'groq', model: 'llama-3.3-70b-versatile', label: 'Groq — Llama 3.3 70B' },
    { provider: 'groq', model: 'qwen/qwen3-32b', label: 'Groq — Qwen3 32B' },
    { provider: 'groq', model: 'openai/gpt-oss-safeguard-20b', label: 'Groq — GPT-OSS 20B (Safety)' },
    { provider: 'cerebras', model: 'llama3.1-8b', label: 'Cerebras — Llama 3.1 8B' },
    { provider: 'cerebras', model: 'qwen-3-235b-a22b-instruct-2507', label: 'Cerebras — Qwen 3 235B A22B' },
    { provider: 'mistral', model: 'mistral-small-latest', label: 'Mistral — Small 3.1' },
    { provider: 'mistral', model: 'mistral-large-latest', label: 'Mistral — Large' },
    { provider: 'sambanova', model: 'Meta-Llama-3.3-70B-Instruct', label: 'SambaNova — Llama 3.3 70B' },
    { provider: 'sambanova', model: 'DeepSeek-V3.1', label: 'SambaNova — DeepSeek V3.1' },
    { provider: 'sambanova', model: 'Llama-4-Maverick-17B-128E-Instruct', label: 'SambaNova — Llama 4 Maverick 17B' },
    { provider: 'openrouter', model: 'google/gemini-2.0-flash-exp:free', label: 'OpenRouter — Gemini 2.0 Flash (Free)' },
    { provider: 'openrouter', model: 'meta-llama/llama-3.3-70b-instruct:free', label: 'OpenRouter — Llama 3.3 70B (Free)' },
];

export const CATEGORIES = [
    { id: 'tables', label: 'Tables', singular: 'Table' },
    { id: 'columns', label: 'Columns', singular: 'Column' },
    { id: 'metrics', label: 'Measures', singular: 'Measure' },
    { id: 'relationships', label: 'Relationships', singular: 'Relationship' },
];

const PROPERTY_LABELS = { definition: 'expression', data_type: 'type', column_count: 'columns', metric_count: 'measures', relationship_count: 'relationships' };
export const propertyLabel = (field) => PROPERTY_LABELS[field] || String(field || '').replace(/_/g, ' ');

/** Display name for one diffed entity of a category. */
export function entityName(category, item) {
    if (category === 'columns' || category === 'metrics') {
        return item.table ? `${item.table}.${item.name}` : (item.name || item._id);
    }
    if (category === 'relationships') {
        if (item.left_table && item.right_table) {
            return `${item.left_table}.${item.left_column || '?'} → ${item.right_table}.${item.right_column || '?'}`;
        }
        return item.name || item._id;
    }
    return item.name || item._id;
}

/**
 * Flattens one category of the /comparator/compare response into table rows:
 * { key, name, property, v1, v2, status, item, first }
 * A changed entity yields one row per changed property.
 */
export function buildRows(category, items, showAll) {
    const rows = [];
    for (const item of items || []) {
        const status = item._diff_status;
        if (!showAll && status === 'identical') continue;
        const name = entityName(category, item);
        const base = { name, status, item };
        if (status === 'modified' && Array.isArray(item._changes) && item._changes.length) {
            item._changes.forEach((ch, i) => rows.push({
                ...base, key: `${item._id}:${ch.field}:${i}`, first: i === 0,
                property: propertyLabel(ch.field), v1: ch.old_value, v2: ch.new_value,
            }));
        } else if (status === 'only_in_1') {
            rows.push({ ...base, key: item._id, first: true, property: '—', v1: 'present', v2: null, missing: 2 });
        } else if (status === 'only_in_2') {
            rows.push({ ...base, key: item._id, first: true, property: '—', v1: null, v2: 'present', missing: 1 });
        } else {
            rows.push({ ...base, key: item._id, first: true, property: '—', v1: 'same', v2: 'same', same: true });
        }
    }
    return rows;
}

/** Number of non-identical entities in a category. */
export const changedCount = (items) => (items || []).filter((i) => i._diff_status !== 'identical').length;
