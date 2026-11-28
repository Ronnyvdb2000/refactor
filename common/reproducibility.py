"""Fingerprint source files and recursive requirements includes for frozen runs."""
import hashlib
from pathlib import Path


def source_fingerprint(root, paths):
    root = Path(root).resolve()
    files = set()

    def include(relative, parents=()):
        relative = Path(relative)
        absolute = (root / relative).resolve()
        name = absolute.relative_to(root).as_posix()
        if name in parents:
            raise ValueError('Cyclische requirements-includes')
        if name in files:
            return
        content = absolute.read_text()
        if absolute.name.startswith('requirements') and absolute.suffix == '.txt':
            for raw in content.splitlines():
                line = raw.split('#', 1)[0].strip()
                if line.startswith(('-r ', '-c ')):
                    include(relative.parent / line[3:].strip(), (*parents, name))
        files.add(name)

    for path in [*paths, 'common/reproducibility.py']:
        include(path)
    digest = hashlib.sha256()
    for name in sorted(files):
        payload = (root/name).read_bytes()
        digest.update(name.encode() + b'\0' + str(len(payload)).encode() + b'\0' + payload)
    return digest.hexdigest()
