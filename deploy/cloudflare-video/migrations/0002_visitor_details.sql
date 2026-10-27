-- Per-visit details, to tell people from bots. Added to the 0001 table; rows from before it keep empty fields (asn 0).
-- Stored: city, region, the network's name (org) and number (asn) as Cloudflare reports them for the connection, three coarse words
-- for the device (device, os, browser), why a visit is a bot (reason: ua, datacenter, scanner, no-accept-language, head,
-- verified-bot; empty for a human), and a daily-changing pseudonymous visitor hash (16 hex characters of an HMAC of the IP with a
-- per-UTC-day salt, so it cannot be followed across days or turned back into the IP). All of it is deleted after 30 days with the row.
-- Never stored: the IP address, the full user agent, cookies.
ALTER TABLE events ADD COLUMN city TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN region TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN asn INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events ADD COLUMN org TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN device TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN os TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN browser TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN reason TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN visitor TEXT NOT NULL DEFAULT '';
-- Unique humans per day, and "has this visitor been counted today".
CREATE INDEX IF NOT EXISTS events_visitor ON events (visitor, ts);
