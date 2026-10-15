"""Merge the installed Debian runtime inventory into the release SBOM."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

path = Path(sys.argv[1])
sbom = json.loads(path.read_text())
rows = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Version}\n'], text=True)
(path.parent / 'runtime-packages.tsv').write_text(rows)
for row in sorted(rows.splitlines()):
    name, version = row.split('\t')
    component = dict(type='library', name=name, version=version,
                     **{'bom-ref': f'debian:{name}:{version}'})
    copyright_file = Path('/usr/share/doc') / name / 'copyright'
    if copyright_file.is_file():
        component['properties'] = [
            dict(name='gitpickle:license-notice-path', value=str(copyright_file)),
            dict(name='gitpickle:license-notice-sha256',
                 value=hashlib.sha256(copyright_file.read_bytes()).hexdigest())]
    sbom['components'].append(component)
path.write_text(json.dumps(sbom, sort_keys=True, indent=2) + '\n')
