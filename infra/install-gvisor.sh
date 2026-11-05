#!/usr/bin/env bash
# Install the pinned runtime without replacing Docker or restarting containers.
set -euo pipefail
if [ "$(uname -m)" != "x86_64" ]; then
  echo 'This pinned installation targets Linux x86_64.' >&2
  exit 1
fi
reprolab_install_dir=$(mktemp -d)
trap 'rm -rf "$reprolab_install_dir"' EXIT
curl -fsSL --max-time 120 https://github.com/google/gvisor/releases/download/release-20261102.0/gvisor-x86_64.tar.bz2 -o "$reprolab_install_dir/gvisor-x86_64.tar.bz2"
printf '%s  %s\n' '6f6b1a8f0f4665a403238135167d0d990bfaeaff5544abf68f5e0d822b35484a' "$reprolab_install_dir/gvisor-x86_64.tar.bz2" | sha256sum -c -
reprolab_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}
reprolab_runtime_dir=/opt/reprolab-runtime/release-20261102.0
reprolab_root python3 - "$reprolab_install_dir/gvisor-x86_64.tar.bz2" "$reprolab_runtime_dir" <<'PY'
import hashlib, os, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath
root = Path(sys.argv[2])
if any(p.is_symlink() for p in [root, *root.parents]):
    raise SystemExit('Runtime directory must not contain symbolic links.')
if not root.parent.exists():
    root.parent.mkdir(mode=0o755)
    root.parent.chmod(0o755)
created = not root.exists()
root.mkdir(mode=0o755, parents=True, exist_ok=True)
if created:
    root.chmod(0o755)
# The checksum binds the archive. Extract only the runtime, leaving host binaries alone.
with tarfile.open(sys.argv[1]) as archive:
    matches = [m for m in archive if PurePosixPath(m.name).name == 'runsc' and m.isfile()]
    if len(matches) != 1:
        raise SystemExit('Pinned archive does not contain exactly one runsc binary.')
    raw = archive.extractfile(matches[0]).read()
destination = root / 'runsc'
if destination.is_symlink():
    raise SystemExit('Existing runsc path is a symbolic link.')
if destination.exists():
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(raw).digest():
        raise SystemExit('Existing pinned runsc differs; no binary was overwritten.')
else:
    fd, temporary = tempfile.mkstemp(prefix='.runsc-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            os.fchmod(stream.fileno(), 0o755)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
PY
reprolab_repo_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd -- "$reprolab_repo_dir"
reprolab_root python3 -m reprolab.host enable-runtime
"$reprolab_runtime_dir/runsc" --version
reprolab_root /usr/bin/docker --host=unix:///var/run/docker.sock pull python:3.12-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487

# Reload and image pull can complete before the controller's bounded Docker
# health query is responsive. Check the exact sanitized controller path; never
# accept a missing runtime or turn readiness into a successful sandbox result.
reprolab_runtime_ready=false
for reprolab_ready_attempt in 1 2 3 4 5 6; do
  if (cd -- "$reprolab_repo_dir" && python3 -m reprolab.cli doctor); then
    reprolab_runtime_ready=true
    break
  fi
  if [ "$reprolab_ready_attempt" -lt 6 ]; then sleep 2; fi
done
if [ "$reprolab_runtime_ready" != true ]; then
  echo 'Pinned gVisor/image installed, but the controller cannot confirm Docker readiness. No execution fallback is enabled.' >&2
  exit 1
fi
