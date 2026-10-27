-- AI visibility counters for the public site (one row per day x kind x agent x path x status).
-- kind: crawler (AI bot), search_bot (classic search engine), ai_ref (visitor from an AI answer),
--       search_ref (visitor from a search engine). No IPs, cookies or full user agents are stored.
CREATE TABLE IF NOT EXISTS hits (
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  agent TEXT NOT NULL,
  path TEXT NOT NULL,
  status INTEGER NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_seen INTEGER NOT NULL,
  PRIMARY KEY (day, kind, agent, path, status)
);
CREATE INDEX IF NOT EXISTS hits_kind_day ON hits (kind, day);
