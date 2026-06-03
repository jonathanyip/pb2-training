import time

from pb2core.db.session import SessionLocal
from pb2core.ingest import run_one_queued_job
from pb2core.init_db import init_db


def main() -> None:
    init_db()
    while True:
        with SessionLocal() as db:
            had = run_one_queued_job(db)
        time.sleep(0.5 if had else 1.0)


if __name__ == "__main__":
    main()
