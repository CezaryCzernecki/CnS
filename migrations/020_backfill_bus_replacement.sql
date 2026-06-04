BEGIN;

UPDATE disruptions
SET has_bus_replacement = TRUE
WHERE has_bus_replacement = FALSE
  AND (
    message ILIKE '%komunikacja zastępcz%'
    OR message ILIKE '%komunikację zastępcz%'
    OR message ILIKE '%komunikacji zastępcz%'
    OR message ILIKE '%zastępcza komunikacja%'
    OR message ILIKE '%zastępczą komunikacj%'
    OR message ILIKE '%autobus zastępczy%'
    OR message ILIKE '%autobusy zastępcze%'
    OR message ILIKE '%autobusami zastępczymi%'
  );

SELECT
    has_bus_replacement,
    COUNT(*) AS count
FROM disruptions
GROUP BY has_bus_replacement
ORDER BY has_bus_replacement;

COMMIT;
