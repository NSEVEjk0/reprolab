"""Operator-only Vultr configuration and verification; never a candidate runner."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from .evidence import verify_bundle
from .evaluator import evaluate
from .fixtures import cases
from .provider import DEFAULT_MODEL
from .sandbox import Sandbox

CONFIG = Path('/etc/reprolab/reprolab.env')
MODELS_URL = 'https://api.vultrinference.com/v1/models'


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def target(ip: str, hostname: str | None) -> tuple[str, str]:
    address = ipaddress.ip_address(ip)
    require(address.version == 4 and address.is_global, 'Use the Vultr instance public IPv4 address.')
    hostname = (hostname or f'{address}.sslip.io').lower().rstrip('.')
    labels = hostname.split('.')
    require(len(hostname) <= 253 and len(labels) >= 2 and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels
    ), 'Supply a DNS hostname, without a scheme, path or port.')
    return str(address), hostname


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        require(not path.is_symlink(), 'The configuration path is a symbolic link.')
        return {}
    require(path.is_file() and not path.is_symlink(), 'Use a regular private configuration file.')
    require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, 'Configuration must have mode 0600.')
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=(.*)', line)
        require(bool(match), f'Invalid configuration syntax on line {number}; values were not printed.')
        key, value = match.groups()
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
            require(isinstance(value, str), 'Environment values must be strings.')
        elif value.startswith("'"):
            parts = shlex.split(value)
            require(len(parts) == 1, 'Invalid quoted environment value.')
            value = parts[0]
        require('\x00' not in value and '\n' not in value and '\r' not in value,
                'Environment values must not contain control characters.')
        values[key] = value
    return values


def private_write(path: Path, raw: bytes) -> None:
    require(not path.is_symlink(), 'Refusing to replace a symbolic link.')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reprolab-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_write(path: Path, value: dict) -> None:
    private_write(path, (json.dumps(value, indent=2) + '\n').encode())


def fetch_json(url: str, *, key: str = '', timeout: float = 12) -> dict:
    headers = {'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'The service rejected the request (HTTP {error.code}); no credentials were printed.') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('The service could not be reached within its deadline.') from None
    require(len(raw) <= 1024 * 1024, 'Service response exceeded the size limit.')
    value = json.loads(raw)
    require(isinstance(value, dict), 'Expected a JSON object from the service.')
    return value


def validate_provider(key: str, model: str) -> None:
    require(16 <= len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key),
            'A Vultr Serverless Inference subscription API key is required.')
    require(bool(re.fullmatch(r'[a-zA-Z0-9._:/-]{1,160}', model)), 'Invalid inference model ID.')
    data = fetch_json(MODELS_URL, key=key)
    require(any(item.get('id') == model for item in data.get('data', []) if isinstance(item, dict)),
            'The configured model is absent from the current Vultr inference model list.')


def configure(args) -> None:
    ip, hostname = target(args.ip, args.hostname)
    values = read_env(args.config)
    key = values.get('VULTR_INFERENCE_API_KEY', '')
    if not key:
        key = getpass.getpass('Vultr Serverless Inference API key (typing is hidden): ').strip()
    model = values.get('VULTR_INFERENCE_MODEL', DEFAULT_MODEL)
    validate_provider(key, model)  # A read-only model lookup, not a paid repair.
    access = values.get('REPROLAB_ACCESS_CODE') or secrets.token_urlsafe(24)
    require(bool(re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', access)), 'Use a bounded alphanumeric demo access code.')
    configured = {
        'VULTR_INFERENCE_API_KEY': key, 'VULTR_INFERENCE_MODEL': model,
        'REPROLAB_STATE_DIR': str(args.state), 'REPROLAB_HOST': '127.0.0.1',
        'REPROLAB_PORT': '8080', 'REPROLAB_ORIGIN': 'https://' + hostname,
        'REPROLAB_ACCESS_CODE': access, 'REPROLAB_PUBLIC_JOBS': 'false',
        'REPROLAB_DAILY_JOB_LIMIT': '8',
    }
    raw = ''.join(name + '=' + json.dumps(value) + '\n' for name, value in configured.items()).encode()
    private_write(args.config, raw)
    require(read_env(args.config) == configured, 'Private configuration readback failed.')
    private_write(args.config.parent / 'demo-access.txt', (access + '\n').encode())
    json_write(args.config.parent / 'deployment.json', {
        'provider': 'Vultr', 'hosting_source': 'operator-supplied Vultr instance; expected public IP observed on host',
        'ip': ip, 'hostname': hostname, 'origin': configured['REPROLAB_ORIGIN'],
        'model': model, 'public_jobs': False, 'daily_job_limit': 8,
        'source_commit': args.commit, 'host_attestation': False,
    })
    print('Private Vultr inference configuration saved; model listing access confirmed.')
    print('Demo access code is in ' + str(args.config.parent / 'demo-access.txt'))


def preflight(args) -> None:
    ip, hostname = target(args.ip, args.hostname)
    require(sys.version_info >= (3, 12), 'The controller needs Python 3.12 or newer.')
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            name, value = line.split('=', 1)
            release[name] = value.strip('"')
    require(release.get('ID') == 'ubuntu', 'This installer targets Ubuntu.')
    require(os.uname().machine == 'x86_64', 'Pinned gVisor and images require x86_64.')
    require((os.cpu_count() or 0) >= 2, 'Resize the VM to at least 2 vCPUs.')
    memory_kib = int(re.search(r'^MemTotal:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M).group(1))
    require(memory_kib >= 2_500_000, 'Resize the VM to 4 GB RAM before installation.')
    require(shutil.disk_usage('/').free >= 12 * 1024 ** 3, 'At least 12 GiB free disk is required.')
    observed = subprocess.run(['ip', '-j', 'address', 'show', 'scope', 'global'],
                              capture_output=True, text=True, check=True, timeout=10)
    addresses = {item.get('local') for interface in json.loads(observed.stdout)
                 for item in interface.get('addr_info', [])}
    require(ip in addresses, 'The expected Vultr IP is not on this host. Run this on the new Vultr VM.')
    resolved = {item[4][0] for item in socket.getaddrinfo(hostname, 443, family=socket.AF_INET)}
    require(ip in resolved, 'The HTTPS hostname does not resolve to this instance yet.')
    print(json.dumps({'os': release.get('PRETTY_NAME'), 'python': sys.version.split()[0],
                      'ip': ip, 'hostname': hostname, 'preflight': 'passed'}))


def caddyfile(args) -> None:
    _, hostname = target(args.ip, args.hostname)
    content = (hostname + ' {\n'
               '    request_body {\n        max_size 3KB\n    }\n'
               '    reverse_proxy 127.0.0.1:8080\n'
               '    header {\n        X-Content-Type-Options nosniff\n'
               '        Referrer-Policy no-referrer\n    }\n}\n')
    # This file has no secrets and must be readable by the caddy service.
    private_write(args.output, content.encode())
    args.output.chmod(0o644)


def health(args) -> None:
    values = read_env(args.config)
    origin = values['REPROLAB_ORIGIN']
    deadline = time.monotonic() + 150
    last = 'The HTTPS service is not ready.'
    while time.monotonic() < deadline:
        try:
            result = fetch_json(origin + '/api/health', timeout=8)
            config = fetch_json(origin + '/api/config', timeout=8)
            require(result.get('ok') is True and result.get('sandbox_ready') is True
                    and result.get('inference_configured') is True and config.get('available') is True
                    and config.get('deployment') == 'controller', 'Controller readiness checks have not passed.')
            print('HTTPS, pinned sandbox readiness and live admission are available: ' + origin)
            return
        except (RuntimeError, ValueError) as error:
            last = str(error)
            time.sleep(2)
    raise RuntimeError(last + ' Check TCP 80/443 in the Vultr firewall and Caddy service logs.')


def browser(args) -> None:
    values = read_env(args.config)
    environment = dict(os.environ)
    for name in ['VULTR_INFERENCE_API_KEY', 'REPROLAB_ACCESS_CODE', 'REPROLAB_PUBLIC_JOBS']:
        environment.pop(name, None)
    # Only the demo access code enters the browser verifier, never the provider key.
    environment.update({'REPROLAB_VERIFY_ORIGIN': values['REPROLAB_ORIGIN'],
                        'REPROLAB_VERIFY_ACCESS': values['REPROLAB_ACCESS_CODE'],
                        'NODE_OPTIONS': '--max-old-space-size=768'})
    result = subprocess.run([args.npm, 'exec', '--', 'playwright', 'test', '--config=playwright.vultr.config.mjs'],
                            env=environment, capture_output=True, text=True, timeout=300)
    output = (result.stdout + result.stderr)[-1024 * 1024:]
    for name in ['VULTR_INFERENCE_API_KEY', 'REPROLAB_ACCESS_CODE']:
        output = output.replace(values[name], '[redacted]')
    print(output, end='')
    require(result.returncode == 0, 'The live browser verification did not pass; no success was recorded.')


def verify_live(args) -> None:
    values = read_env(args.config)
    raw = args.evidence.read_text()
    for name in ['VULTR_INFERENCE_API_KEY', 'REPROLAB_ACCESS_CODE']:
        require(values[name] not in raw, 'A credential appeared in evidence; do not publish it.')
    bundle = json.loads(raw)
    verify_bundle(bundle)
    payload = bundle['payload']
    require(payload.get('evidence_origin') == 'live-vultr-job' and payload.get('status') == 'verified',
            'The real Vultr repair was not verified; infrastructure examples are not a fallback.')
    provider = payload['candidate']['provider']
    require(provider.get('name') == 'vultr-serverless-inference'
            and provider.get('model') == values['VULTR_INFERENCE_MODEL'], 'Unexpected inference provider or model.')
    require(payload['evaluation']['passed'] == 20 and payload['evaluation']['failed'] == 0,
            'The real repair did not pass all 20 independent cases.')
    sandbox = Sandbox(work_root=args.state / 'work')
    sandbox.ready()
    inputs = cases(payload['fixture_id'], payload['evaluation']['seed'])
    result = evaluate(payload['fixture_id'], inputs, sandbox.run(payload['candidate']['code'], inputs))
    require(result['valid_protocol'] and result['failed'] == 0 and result['passed'] == 20
            and result['teardown']['confirmed'], 'Fresh gVisor replay or teardown did not pass.')
    json_write(args.output, {'live_evidence_sha256': bundle['sha256'], 'fixture_id': payload['fixture_id'],
                            'provider': provider, 'replay': {'passed': result['passed'], 'failed': result['failed'],
                            'valid_protocol': result['valid_protocol'], 'teardown': result['teardown']}})
    print('One real Vultr repair, independent evaluation, evidence integrity and fresh sandbox replay passed.')


def status_command(args) -> None:
    json_write(args.output, {'state': args.state, 'phase': args.phase, 'host': args.ip,
                            'hostname': args.hostname, 'source_commit': args.commit,
                            'updated_at_unix': int(time.time())})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['preflight', 'configure', 'caddyfile']:
        command = commands.add_parser(name)
        command.add_argument('--ip', required=True)
        command.add_argument('--hostname')
        if name == 'configure':
            command.add_argument('--config', type=Path, default=CONFIG)
            command.add_argument('--state', type=Path, default=Path('/var/lib/reprolab'))
            command.add_argument('--commit', required=True)
        if name == 'caddyfile':
            command.add_argument('--output', type=Path, required=True)
    for name in ['health', 'browser', 'verify-live']:
        command = commands.add_parser(name)
        command.add_argument('--config', type=Path, default=CONFIG)
        if name == 'browser':
            command.add_argument('--npm', required=True)
        if name == 'verify-live':
            command.add_argument('--evidence', type=Path, required=True)
            command.add_argument('--state', type=Path, default=Path('/var/lib/reprolab'))
            command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('status')
    command.add_argument('--output', type=Path, default=Path('/etc/reprolab/install-status.json'))
    command.add_argument('--state', required=True)
    command.add_argument('--phase', required=True)
    command.add_argument('--ip', required=True)
    command.add_argument('--hostname', required=True)
    command.add_argument('--commit', required=True)
    args = parser.parse_args()
    functions = {'preflight': preflight, 'configure': configure, 'caddyfile': caddyfile,
                 'health': health, 'browser': browser, 'verify-live': verify_live, 'status': status_command}
    functions[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ReproLab setup stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
