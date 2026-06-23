#!/bin/bash
# Wrapper dla crona — uruchamia walidator rankingów z hosta (poza kontenerami).
#
# Strategia połączenia z bazą:
#   Jeśli docker-compose.yml ma ports: "127.0.0.1:5432:5432" → używa localhost.
#   Jeśli baza jest tylko przez expose (bez port mappingu) → używa docker exec psycopg.
#
# Obecnie: baza ma port mapping 127.0.0.1:5432:5432, więc łączymy bezpośrednio.
#
# Cron (crontab -e):
#   0 6,12,18,0 * * * /home/cezary/cns/CnS/scripts/validate_rankings_daily.sh
set -euo pipefail

REPO=/home/cezary/cns/CnS
PYTHON=/home/cezary/.cache/pypoetry/virtualenvs/cyrk-na-szynach-KunUUDu--py3.12/bin/python

cd "$REPO"

# Próbujemy załadować DATABASE_URL z .env i podmienić hostname "db" na 127.0.0.1,
# bo z hosta (poza Docker) "db" nie jest rozwiązywalne — potrzebujemy localhost.
if [ -f .env ]; then
    RAW_URL=$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)
    # Zamień @db: na @127.0.0.1: (Docker hostname → localhost)
    export DATABASE_URL="${RAW_URL/@db:/@127.0.0.1:}"
else
    # Fallback: znane kredencjały z docker-compose.yml
    export DATABASE_URL="postgresql://cyrk_na_szynach:cyrk_na_szynach@127.0.0.1:5432/cyrk_na_szynach"
fi

exec "$PYTHON" scripts/validate_rankings_daily.py >> logs/validator.log 2>&1
