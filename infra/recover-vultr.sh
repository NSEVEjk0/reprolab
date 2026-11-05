#!/usr/bin/env bash
# Resume a completed infrastructure setup after a rejected inference credential.
set -euo pipefail
umask 077
if [ "$(id -u)" -ne 0 ] || [ ! -t 0 ]; then
  echo 'Run this as root in the interactive Vultr terminal.' >&2
  exit 2
fi
cd /opt/reprolab
reprolab_phase='private-key-replacement'
reprolab_recovery_failed() {
  echo "Recovery stopped during $reprolab_phase; no completion was recorded." >&2
  if [ "$reprolab_phase" = 'one-real-vultr-repair' ]; then
    python3 - <<'PY' || true
import json, sqlite3
from reprolab.deploy import CONFIG, read_env
try:
    values = read_env(CONFIG)
    with sqlite3.connect('file:/var/lib/reprolab/jobs.sqlite3?mode=ro', uri=True) as db:
        row = db.execute('SELECT status,message,evidence FROM jobs ORDER BY created_at DESC LIMIT 1').fetchone()
    if row:
        message = str(row[1] or '')
        for name in ('VULTR_INFERENCE_API_KEY', 'REPROLAB_ACCESS_CODE'):
            if values.get(name):
                message = message.replace(values[name], '[redacted]')
        print('Latest job:', row[0])
        print('Reason:', ''.join(char for char in message if char.isprintable())[:1000])
        payload = json.loads(row[2])['payload'] if row[2] else {}
        for name in ('baseline', 'evaluation'):
            result = payload.get(name, {})
            print(name + ':', {key: result.get(key) for key in ('passed', 'failed', 'exit_code')})
        print('Repair attempts:', len(payload.get('attempts', [])))
except Exception:
    print('Saved-job diagnostics unavailable; no new inference request was made.')
PY
  fi
}
trap reprolab_recovery_failed ERR
python3 - <<'PY'
import getpass, json, re, urllib.error, urllib.request, warnings
from pathlib import Path
from reprolab.deploy import CONFIG, private_write, read_env, require, target, validate_provider
def replace_deployed_key():
    status = json.loads(Path('/etc/reprolab/install-status.json').read_text())
    metadata = json.loads(Path('/etc/reprolab/deployment.json').read_text())
    require(status.get('state') == 'failed' and status.get('phase') == 'one-real-vultr-repair',
            'This recovery requires completed infrastructure checks and a failed live repair.')
    require(metadata.get('provider') == 'Vultr'
            and status.get('source_commit') == metadata.get('source_commit')
            and re.fullmatch(r'[a-f0-9]{40}', metadata.get('source_commit', '')),
            'The deployment source records do not agree.')
    _, hostname = target(metadata['ip'], metadata['hostname'])
    values = read_env(CONFIG)
    require(values.get('REPROLAB_ORIGIN') == metadata.get('origin') == 'https://' + hostname,
            'The deployment HTTPS origin records do not agree.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            key = getpass.getpass('Replacement subscription API key (hidden): ').strip()
        except getpass.GetPassWarning:
            raise RuntimeError('Hidden input is unavailable; use the interactive Vultr terminal.') from None
    require(16 <= len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key),
            'Enter only the subscription API key copied from Overview.')
    request = urllib.request.Request('https://api.vultrinference.com/v1/health',
                                     headers={'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            require(response.status == 200, 'Authenticated inference health did not pass.')
            require(len(response.read(65537)) <= 65536, 'Health response exceeded its limit.')
    except urllib.error.HTTPError as error:
        error.close()
        raise RuntimeError('Vultr rejected the replacement key (HTTP ' + str(error.code)
                           + '). The existing configuration was kept.') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Vultr health could not be reached. The existing configuration was kept.') from None
    # Compatible with the previously deployed model-only validator, as well as the fixed one.
    validate_provider(key, values['VULTR_INFERENCE_MODEL'])
    values['VULTR_INFERENCE_API_KEY'] = key
    private_write(CONFIG, ''.join(name + '=' + json.dumps(value) + '\n'
                                  for name, value in values.items()).encode())
    require(read_env(CONFIG) == values, 'Private key replacement readback failed.')
    print('Authenticated replacement key saved privately. It was not printed.')

if __name__ == '__main__':
    try:
        replace_deployed_key()
    except Exception as error:
        raise SystemExit('Key recovery stopped: ' + str(error)) from None
PY
reprolab_phase='reprolab-service-restart'
systemctl restart reprolab.service reprolab-https.service
reprolab_phase='https-health'
python3 -m reprolab.deploy health
reprolab_phase='one-real-vultr-repair'
export PATH="/opt/reprolab/.local/node-v22.23.4/bin:$PATH"
python3 -m reprolab.deploy browser --npm /opt/reprolab/.local/node-v22.23.4/bin/npm
reprolab_phase='fresh-evidence-replay'
python3 -m reprolab.deploy verify-live \
  --evidence reports/live-vultr-evidence.json --output reports/live-vultr-replay.json
reprolab_phase='existing-project-preservation'
python3 -m reprolab.host verify
python3 - <<'PY'
import argparse, json
from pathlib import Path
from reprolab.deploy import status_command
metadata = json.loads(Path('/etc/reprolab/deployment.json').read_text())
status_command(argparse.Namespace(state='complete', phase='verified', ip=metadata['ip'],
    hostname=metadata['hostname'], commit=metadata['source_commit'],
    output=Path('/etc/reprolab/install-status.json')))
print('ReproLab live repair, evidence replay and host preservation verified: '
      + metadata['origin'])
PY
trap - ERR
