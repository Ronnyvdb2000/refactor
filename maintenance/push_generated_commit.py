"""Publish an already committed workflow output without overwriting remote changes.

Only ordinary fast-forward pushes are used. Rebase conflicts stop publication;
there is no forced push or automatic choice of one side of a conflict.
"""
import argparse
import os
import subprocess


def git(*args, check=True):
    return subprocess.run(['git', *args], check=check, text=True, capture_output=True)


def publish(attempts=3):
    if not 1 <= attempts <= 5:
        raise ValueError('attempts must be between 1 and 5')
    repository = os.getenv('GITHUB_REPOSITORY')
    if repository and repository.casefold() != 'ronnyvdb2000/refactor':
        raise RuntimeError('This publication helper is restricted to Ronnyvdb2000/refactor')
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise RuntimeError('Tracked changes remain uncommitted; publication stopped')
    for attempt in range(1, attempts + 1):
        git('fetch', 'origin', 'main')
        ahead = int(git('rev-list', '--count', 'origin/main..HEAD').stdout.strip())
        if not ahead:
            print('Geen nieuwe commit om te publiceren.', flush=True)
            return
        result = git('rebase', 'origin/main', check=False)
        if result.returncode:
            git('rebase', '--abort', check=False)
            raise RuntimeError('Rebaseconflict; wijzigingen niet automatisch overschreven')
        result = git('push', 'origin', 'HEAD:main', check=False)
        if not result.returncode:
            print('Gegenereerde commit gepubliceerd.', flush=True)
            return
        print(f'Push geweigerd (poging {attempt}/{attempts}); remote opnieuw controleren.', flush=True)
    raise RuntimeError('Publicatie mislukt na begrensde herpogingen; geen geforceerde push')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args()
    publish(args.attempts)


if __name__ == '__main__':
    main()
