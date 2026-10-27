-- The video link's counters. No IP address, full user agent or cookie is stored anywhere in this database. Migration 0002 adds
-- city, network, coarse device, the bot reason and a daily-changing pseudonymous visitor hash to events; see its comment.

-- One row per UTC day, source and kind ("human" or "bot"), counted with an UPSERT.
CREATE TABLE IF NOT EXISTS clicks (
  day TEXT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, source, kind)
);

-- One row per counted visit: time, source, two-letter country and kind (0002 adds the details). Rows older than 30 days are deleted by the daily cron.
CREATE TABLE IF NOT EXISTS events (
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  country TEXT NOT NULL,
  kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

-- Emails sent per UTC day: visit alerts (capped) and whether that day's summary went out (once).
CREATE TABLE IF NOT EXISTS mail (
  day TEXT PRIMARY KEY,
  visits INTEGER NOT NULL DEFAULT 0,
  summary INTEGER NOT NULL DEFAULT 0
);
