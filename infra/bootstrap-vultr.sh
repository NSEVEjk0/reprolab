#!/usr/bin/env bash
# Download an exact reviewed ReproLab revision on the user's dedicated Vultr VM.
set -euo pipefail
umask 077
reprolab_commit=''
reprolab_archive_sha=''
reprolab_ip=''
reprolab_hostname=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit) reprolab_commit=${2:?Missing commit}; shift 2 ;;
    --archive-sha256) reprolab_archive_sha=${2:?Missing archive checksum}; shift 2 ;;
    --ip) reprolab_ip=${2:?Missing IP}; shift 2 ;;
    --hostname) reprolab_hostname=${2:?Missing hostname}; shift 2 ;;
    *) echo "Unknown bootstrap argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -ne 0 ] || ! [[ "$reprolab_commit" =~ ^[a-f0-9]{40}$ ]] || \
   ! [[ "$reprolab_archive_sha" =~ ^[a-f0-9]{64}$ ]] || [ -z "$reprolab_ip" ]; then
  echo 'Run as root with --commit SHA --archive-sha256 SHA256 --ip PUBLIC_IPV4.' >&2
  exit 2
fi
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required"'
if [ "$(uname -m)" != 'x86_64' ]; then echo 'Use an x86_64 Vultr VM.' >&2; exit 1; fi
reprolab_hostname=${reprolab_hostname:-$reprolab_ip.sslip.io}
reprolab_download=$(mktemp -d /tmp/reprolab-bootstrap.XXXXXX)
trap 'rm -rf -- "$reprolab_download"' EXIT
reprolab_archive="$reprolab_download/source.tar.gz"
curl -fsSL --retry 2 --max-time 180 "https://codeload.github.com/NSEVEjk0/reprolab/tar.gz/$reprolab_commit" -o "$reprolab_archive"
printf '%s  %s\n' "$reprolab_archive_sha" "$reprolab_archive" | sha256sum -c -
python3 - "$reprolab_archive" <<'PY'
import sys,tarfile
from pathlib import Path,PurePosixPath
with tarfile.open(sys.argv[1]) as archive:
    for item in archive:
        path=PurePosixPath(item.name)
        if path.is_absolute() or '..' in path.parts or item.issym() or item.islnk() or not (item.isfile() or item.isdir()):
            raise SystemExit('Unsafe archive member; extraction stopped.')
        destination=Path('/opt/reprolab').joinpath(*path.parts[1:])
        for candidate in [destination,*destination.parents]:
            if candidate.is_symlink(): raise SystemExit('Existing source path is a symlink; extraction stopped.')
            if candidate==Path('/opt/reprolab'): break
PY
if [ -L /opt/reprolab ]; then echo 'The application directory must not be a symlink.' >&2; exit 1; fi
if [ -d /opt/reprolab ] && [ -n "$(find /opt/reprolab -mindepth 1 -maxdepth 1 -print -quit)" ] && \
   [ ! -f /etc/reprolab/deployment.json ]; then
  if [ ! -f /etc/reprolab/bootstrap.json ]; then
    echo '/opt/reprolab contains an unrecognized installation; no files were overwritten.' >&2
    exit 1
  fi
  python3 -c "import json; v=json.load(open('/etc/reprolab/bootstrap.json')); assert v.get('repository') == 'NSEVEjk0/reprolab'"
fi
if systemctl is-active --quiet reprolab.service; then systemctl stop reprolab.service; fi
install -d -m 0755 /opt/reprolab
install -d -m 0700 /etc/reprolab
python3 - "$reprolab_commit" <<'PY'
import json,os,sys
fd=os.open('/etc/reprolab/bootstrap.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as stream:
    json.dump({'repository':'NSEVEjk0/reprolab','source_commit':sys.argv[1]},stream)
    stream.write('\n')
PY
tar --extract --gzip --file "$reprolab_archive" --directory /opt/reprolab \
  --strip-components=1 --no-same-owner --no-same-permissions
python3 - "$reprolab_archive" <<'PY'
import os,sys,tarfile
from pathlib import Path,PurePosixPath
# Set readable modes only on reviewed archive members, never on private state,
# operator keys, dependency caches, reports or other files from a previous run.
with tarfile.open(sys.argv[1]) as archive:
    for item in archive:
        parts=PurePosixPath(item.name).parts[1:]
        if not parts: continue
        destination=Path('/opt/reprolab').joinpath(*parts)
        if destination.is_symlink(): raise SystemExit('Unexpected source symlink.')
        os.chmod(destination,0o755 if item.isdir() or destination.suffix=='.sh' else 0o644)
PY
rm -rf -- "$reprolab_download"
trap - EXIT
exec bash /opt/reprolab/infra/setup-vultr.sh --commit "$reprolab_commit" \
  --ip "$reprolab_ip" --hostname "$reprolab_hostname"
