"""Run a model only while its exact Supabase input manifest remains released."""
import argparse
import os
import subprocess
import sys

from common.feature_pipeline import load_feature_context
from common.run_manifest import ready_input


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module')
    args,forwarded=parser.parse_known_args()
    if not args.module.startswith(('models.','meta_models.')):
        parser.error('Alleen modelmodules zijn toegestaan')
    context=load_feature_context()
    if context is None:
        raise RuntimeError('Bevestigd featuremanifest ontbreekt')
    import psycopg2
    conn=psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=30)
    try:
        with ready_input(conn,context):
            return subprocess.run([sys.executable,'-m',args.module,*forwarded],check=False).returncode
    finally:
        conn.close()


if __name__=='__main__':
    sys.exit(main())
