BEGIN IMMEDIATE;

DROP TABLE IF EXISTS minutes;
DROP TABLE IF EXISTS minutes_fts;

CREATE TABLE minutes (
    id INTEGER PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    meeting_name TEXT,
    year_label TEXT NOT NULL,
    held_on TEXT,
    gregorian_year INTEGER,
    month INTEGER,
    day INTEGER,
    doc_type TEXT NOT NULL,
    ext TEXT NOT NULL,
    source_fino INTEGER,
    source_year INTEGER,
    source_url TEXT,
    content TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE minutes_fts USING fts5(
    title,
    meeting_name,
    content,
    content='minutes',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE INDEX idx_minutes_held_on ON minutes(held_on);
CREATE INDEX idx_minutes_doc_type ON minutes(doc_type);
CREATE INDEX idx_minutes_source_fino ON minutes(source_fino);
CREATE INDEX idx_minutes_year_label ON minutes(year_label);

COMMIT;