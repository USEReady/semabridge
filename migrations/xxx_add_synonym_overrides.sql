-- migrations/xxx_add_synonym_overrides.sql
CREATE TABLE IF NOT EXISTS synonym_overrides (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    project_id  TEXT NOT NULL,
    model_name  TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL,
    synonyms    JSON NOT NULL DEFAULT '[]',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, model_name, table_name, column_name)
);
 
-- Auto-update updated_at on change
CREATE TRIGGER IF NOT EXISTS synonym_overrides_updated_at
    AFTER UPDATE ON synonym_overrides
BEGIN
    UPDATE synonym_overrides SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;
