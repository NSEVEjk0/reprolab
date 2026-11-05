#!/usr/bin/env bash
# Run on the dedicated Vultr VM from its ordinary terminal or web console.
set -euo pipefail
umask 077
reprolab_ip=''
reprolab_hostname=''
reprolab_commit=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ip) reprolab_ip=${2:?Missing IP}; shift 2 ;;
    --hostname) reprolab_hostname=${2:?Missing hostname}; shift 2 ;;
    --commit) reprolab_commit=${2:?Missing commit}; shift 2 ;;
    *) echo "Unknown installer argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -ne 0 ] || [ -z "$reprolab_ip" ] || ! [[ "$reprolab_commit" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'Run as root with --ip PUBLIC_IPV4 --commit EXACT_SOURCE_COMMIT.' >&2
  exit 2
fi
reprolab_repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd -- "$reprolab_repo"
reprolab_hostname=${reprolab_hostname:-$reprolab_ip.sslip.io}
reprolab_phase='preflight'
reprolab_failed() {
  python3 -m reprolab.deploy status --state failed --phase "$reprolab_phase" \
    --ip "$reprolab_ip" --hostname "$reprolab_hostname" --commit "$reprolab_commit" || true
  echo "Setup stopped during $reprolab_phase. Review /var/log/reprolab-install.log and /etc/reprolab/install-status.json." >&2
}
trap reprolab_failed ERR
python3 -m reprolab.deploy preflight --ip "$reprolab_ip" --hostname "$reprolab_hostname"
install -d -m 0700 /etc/reprolab
if [ -f /etc/reprolab/deployment.json ]; then
  python3 -c "import json; v=json.load(open('/etc/reprolab/deployment.json')); assert v.get('provider') == 'Vultr'"
fi
if command -v docker >/dev/null 2>&1; then
  reprolab_containers=$(docker --host=unix:///var/run/docker.sock ps --all --format '{{.ID}} {{.Label "io.reprolab.owned"}}')
  while read -r reprolab_container reprolab_owned; do
    if [ -n "$reprolab_container" ] && [ "$reprolab_owned" != 'true' ]; then
      echo 'This VM contains other projects containers. Use a dedicated Vultr VM.' >&2
      exit 1
    fi
  done <<< "$reprolab_containers"
fi
reprolab_phase='private-inference-configuration'
python3 -m reprolab.deploy configure --ip "$reprolab_ip" --hostname "$reprolab_hostname" --commit "$reprolab_commit"

reprolab_phase='system-packages'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends docker.io caddy python3 python3-venv \
  ca-certificates curl sudo bzip2 xz-utils tar
systemctl enable --now docker.service
systemctl stop caddy.service
if systemctl is-active --quiet reprolab.service; then systemctl stop reprolab.service; fi
if systemctl is-active --quiet reprolab-gc.timer; then systemctl stop reprolab-gc.timer; fi
if ! id reprolab >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/reprolab --no-create-home --shell /usr/sbin/nologin reprolab
fi
usermod -aG docker reprolab
install -d -o reprolab -g reprolab -m 0700 /var/lib/reprolab /var/lib/reprolab/work
install -d -m 0700 .local reports
touch /var/log/reprolab-install.log
chmod 0600 /var/log/reprolab-install.log
# Configure has completed, so no interactive secret input enters this log.
exec > >(tee -a /var/log/reprolab-install.log) 2>&1

reprolab_phase='pinned-gvisor'
bash infra/install-gvisor.sh
reprolab_phase='unit-and-containment-checks'
python3 -m unittest tests.test_unit tests.test_deploy -v
REPROLAB_INTEGRATION=true python3 -m unittest tests.test_integration -v
python3 -c "from pathlib import Path; from reprolab.sandbox import Sandbox; print(Sandbox(work_root=Path('/var/lib/reprolab/work')).ready())"

reprolab_phase='browser-tooling'
reprolab_node_version='v22.23.4'
reprolab_node_sha='1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce'
reprolab_node_dir="$reprolab_repo/.local/node-$reprolab_node_version"
if [ ! -x "$reprolab_node_dir/bin/node" ]; then
  reprolab_node_archive="$reprolab_repo/.local/node-linux-x64.tar.xz"
  curl -fsSL --retry 2 --max-time 180 "https://nodejs.org/dist/$reprolab_node_version/node-$reprolab_node_version-linux-x64.tar.xz" -o "$reprolab_node_archive"
  printf '%s  %s\n' "$reprolab_node_sha" "$reprolab_node_archive" | sha256sum -c -
  install -d -m 0755 "$reprolab_node_dir"
  tar --extract --xz --file "$reprolab_node_archive" --directory "$reprolab_node_dir" --strip-components=1 --no-same-owner
  rm -- "$reprolab_node_archive"
fi
export PATH="$reprolab_node_dir/bin:$PATH"
export NODE_OPTIONS=--max-old-space-size=768
node --version
npm ci --include=dev --no-audit --no-fund
node --check web/app.js
node --check tests/browser/live-vultr.spec.mjs
npm exec -- playwright install --with-deps --only-shell chromium

reprolab_phase='reference-browser-workflow'
reprolab_browser_state=$(mktemp -d /tmp/reprolab-browser-check.XXXXXX)
env -u VULTR_INFERENCE_API_KEY -u REPROLAB_ACCESS_CODE \
  -u REPROLAB_VERIFY_ORIGIN -u REPROLAB_VERIFY_ACCESS \
  REPROLAB_PUBLIC_JOBS=false REPROLAB_STATE_DIR="$reprolab_browser_state" \
  REPROLAB_HOST=127.0.0.1 REPROLAB_PORT=8080 REPROLAB_ORIGIN=http://127.0.0.1:8080 \
  npm exec -- playwright test --config=playwright.config.mjs
rm -rf -- "$reprolab_browser_state"

reprolab_phase='controller-and-https'
install -o root -g root -m 0644 infra/reprolab.service /etc/systemd/system/reprolab.service
install -o root -g root -m 0644 infra/reprolab-gc.service /etc/systemd/system/reprolab-gc.service
install -o root -g root -m 0644 infra/reprolab-gc.timer /etc/systemd/system/reprolab-gc.timer
python3 -m reprolab.deploy caddyfile --ip "$reprolab_ip" --hostname "$reprolab_hostname" --output /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
if command -v ufw >/dev/null 2>&1; then
  if [[ "$(ufw status)" == 'Status: active'* ]]; then
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
  fi
fi
systemctl daemon-reload
systemctl enable --now reprolab.service reprolab-gc.timer caddy.service
python3 -m reprolab.deploy health

reprolab_phase='one-real-vultr-repair'
python3 -m reprolab.deploy browser --npm "$reprolab_node_dir/bin/npm"
reprolab_phase='fresh-evidence-replay'
python3 -m reprolab.deploy verify-live --evidence reports/live-vultr-evidence.json --output reports/live-vultr-replay.json
python3 -m reprolab.deploy status --state complete --phase verified \
  --ip "$reprolab_ip" --hostname "$reprolab_hostname" --commit "$reprolab_commit"
trap - ERR
echo "ReproLab verified: https://$reprolab_hostname"
echo 'Your demo access code is in /etc/reprolab/demo-access.txt; provider credentials were not printed.'
echo 'Live evidence, replay and containment reports are in /opt/reprolab/reports/.'
