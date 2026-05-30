"""
TorAlert – punkt wejścia.

Uruchomienie z PostgreSQL:
    poetry run cns --once --verbose

Inicjalizacja bazy:
    poetry run cns db-init
    poetry run cns db-stats

Zmienne środowiskowe (.env):
    PKP_API_KEY=sk_live_...
    DATABASE_URL=postgresql://user:password@localhost:5432/cns
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("cns.log", encoding="utf-8"),
        ],
    )


def cmd_collect(args, api_key: str, storage) -> None:
    from cns.collector.collector import DataCollector
    collector = DataCollector(
        api_key=api_key,
        storage=storage,
        operations_interval_min=args.interval,
        dry_run=args.dry_run,
    )
    if args.once:
        logging.getLogger("cns").info("Tryb jednorazowy (--once)")
        collector.collect_once()
        logging.getLogger("cns").info("Gotowe.")
    else:
        collector.run()


def cmd_db_init(args) -> None:
    """Uruchom migracje SQL na bazie danych."""
    import pathlib
    import psycopg

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Brak DATABASE_URL w .env")
        sys.exit(1)

    migrations_dir = pathlib.Path(__file__).parent.parent / "migrations"
    if not migrations_dir.exists():
        # Szukaj też w bieżącym katalogu
        migrations_dir = pathlib.Path("migrations")

    if not migrations_dir.exists():
        print(f"Nie znaleziono katalogu migrations/")
        sys.exit(1)

    migration_files = sorted(migrations_dir.glob("0*.sql"))
    if not migration_files:
        print("Brak plików migracji w migrations/")
        sys.exit(1)

    with psycopg.connect(database_url) as conn:
        for mf in migration_files:
            # Pomiń TimescaleDB jeśli nie jest dostępne
            if "timescaledb" in mf.name:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")
                        if not cur.fetchone():
                            print(f"  ⏭  Pomijam {mf.name} (TimescaleDB niedostępne)")
                            continue
                except Exception:
                    print(f"  ⏭  Pomijam {mf.name} (TimescaleDB niedostępne)")
                    continue

            print(f"  ▶  Uruchamiam {mf.name}...")
            sql = mf.read_text(encoding="utf-8")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print(f"  ✅ {mf.name} – OK")
            except Exception as e:
                print(f"  ❌ {mf.name} – błąd: {e}")
                conn.rollback()
                sys.exit(1)

    print("\nBaza danych zainicjalizowana ✅")


def cmd_api_serve(args) -> None:
    """Uruchom serwer FastAPI."""
    try:
        import uvicorn
    except ImportError:
        print("Zainstaluj: poetry install -E api")
        sys.exit(1)
    from cns.api.app import app
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


def cmd_db_stats(args) -> None:
    """Wyświetl statystyki bazy danych."""
    from cns.storage.postgres import PostgresStorage

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Brak DATABASE_URL w .env")
        sys.exit(1)

    storage = PostgresStorage(database_url)
    stats = storage.get_stats()

    print("\n📊 Statystyki bazy TorAlert")
    print("─" * 40)
    print(f"  Stacje:          {stats['stations']:>8,}")
    print(f"  Przewoźnicy:     {stats['carriers']:>8,}")
    print(f"  Snapshoty:       {stats['snapshots']:>8,}")
    print(f"  Operacje poc.:   {stats['train_ops']:>8,}")
    print(f"  Przystanki:      {stats['stops']:>8,}")
    print(f"  Utrudnienia:     {stats['disruptions']:>8,}")
    if stats['last_snapshot']:
        print(f"  Ostatni snapshot: {stats['last_snapshot'].strftime('%Y-%m-%d %H:%M:%S')}")
    print()


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="TorAlert – kolekcjoner danych PKP")
    subparsers = parser.add_subparsers(dest="command")

    # Komenda domyślna: zbieranie danych
    collect_parser = subparsers.add_parser("collect", help="Zbieraj dane (domyślna komenda)")
    collect_parser.add_argument("--interval", type=int, default=15)
    collect_parser.add_argument("--output-dir", default="./data")
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.add_argument("--once", action="store_true")
    collect_parser.add_argument("--verbose", "-v", action="store_true")
    collect_parser.add_argument("--no-db", action="store_true",
                                help="Używaj JsonFileStorage zamiast PostgreSQL")

    # db-init
    subparsers.add_parser("db-init", help="Uruchom migracje SQL")

    # db-stats
    subparsers.add_parser("db-stats", help="Statystyki bazy danych")

    # api-serve
    api_parser = subparsers.add_parser("api-serve", help="Uruchom serwer FastAPI")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8000)
    api_parser.add_argument("--reload", action="store_true", help="Auto-reload przy zmianach kodu")

    # Obsługa wywołania bez subkomendy (wsteczna kompatybilność)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--output-dir", default="./data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-db", action="store_true")

    args = parser.parse_args()
    configure_logging(verbose=getattr(args, 'verbose', False))
    logger = logging.getLogger("cns")

    if args.command == "db-init":
        cmd_db_init(args)
        return

    if args.command == "db-stats":
        cmd_db_stats(args)
        return

    if args.command == "api-serve":
        cmd_api_serve(args)
        return

    # Domyślnie: zbieranie danych
    api_key = os.environ.get("PKP_API_KEY")
    if not api_key:
        logger.error("Brak PKP_API_KEY w .env")
        sys.exit(1)

    # Wybór storage
    use_db = not getattr(args, 'no_db', False)
    database_url = os.environ.get("DATABASE_URL")

    if use_db and database_url:
        try:
            from cns.storage.postgres import PostgresStorage
            storage = PostgresStorage(database_url)
            logger.info("Storage: PostgreSQL (%s)", database_url.split("@")[-1])
        except Exception as e:
            logger.warning("Nie można połączyć z PostgreSQL (%s) – używam plików JSON", e)
            from cns.collector.collector import JsonFileStorage
            storage = JsonFileStorage(output_dir=args.output_dir)
    else:
        from cns.collector.collector import JsonFileStorage
        storage = JsonFileStorage(output_dir=args.output_dir)
        logger.info("Storage: JSON files (%s)", args.output_dir)

    cmd_collect(args, api_key, storage)


if __name__ == "__main__":
    main()
