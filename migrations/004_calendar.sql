-- Faza 1.2 – tabela wydarzeń kalendarza
-- Wypełniana przy starcie przez CalendarService.generate_events() na 5 lat naprzód.
-- Aktualizowana automatycznie 1 stycznia każdego roku.

CREATE TABLE IF NOT EXISTS calendar_events (
    id          BIGSERIAL PRIMARY KEY,
    event_date  DATE NOT NULL,
    zone        CHAR(1),              -- 'A' | 'B' | 'C' | NULL = cały kraj
    day_type    VARCHAR(30) NOT NULL, -- wartości enum DayType
    event_name  VARCHAR(100),
    UNIQUE NULLS NOT DISTINCT (event_date, zone)  -- PostgreSQL 15+
);

CREATE INDEX IF NOT EXISTS calendar_events_date_idx
    ON calendar_events (event_date);
