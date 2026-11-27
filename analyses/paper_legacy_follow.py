"""Finish existing v1 baskets without starting any new v1 decisions."""
import json
import os
from analyses import paper_trial_legacy as legacy


def main():
    import psycopg2
    legacy.freeze_batch = lambda *args, **kwargs: None
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=20) as conn:
        result = legacy.run(conn)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
