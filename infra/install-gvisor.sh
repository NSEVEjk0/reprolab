#!/usr/bin/env bash
# Run only on a dedicated Vultr VM or an isolated CI runner.
set -euo pipefail
if [ "$(uname -m)" != "x86_64" ]; then
  echo 'This pinned installation targets Linux x86_64.' >&2
  exit 1
fi
reprolab_install_dir=$(mktemp -d)
trap 'rm -rf "$reprolab_install_dir"' EXIT
curl -fsSL --max-time 120 https://github.com/google/gvisor/releases/download/release-20261102.0/gvisor-x86_64.tar.bz2 -o "$reprolab_install_dir/gvisor-x86_64.tar.bz2"
printf '%s  %s\n' '6f6b1a8f0f4665a403238135167d0d990bfaeaff5544abf68f5e0d822b35484a' "$reprolab_install_dir/gvisor-x86_64.tar.bz2" | sha256sum -c -
sudo tar -xjf "$reprolab_install_dir/gvisor-x86_64.tar.bz2" -C /usr/local/bin
sudo /usr/local/bin/runsc install
sudo systemctl reload docker
/usr/local/bin/runsc --version
docker pull python:3.12-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487
