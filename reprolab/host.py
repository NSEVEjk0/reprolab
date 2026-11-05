"""Operator checks for sharing a Docker host; no candidate execution."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from .deploy import json_write, private_write, require
from .sandbox import capture

DOCKER = '/usr/bin/docker'
DAEMON_CONFIG = Path('/etc/docker/daemon.json')
RUNTIME_BINARY = Path('/opt/reprolab-runtime/release-20261102.0/runsc')
MEMINFO = Path('/proc/meminfo')
CADDY_CONFIG = Path('/etc/caddy/Caddyfile')
HTTPS_CONFIG = Path('/etc/caddy/reprolab.Caddyfile')
CADDY_BINARY = Path('/usr/bin/caddy')
INFO_FORMAT = (
    '{"id":{{json .ID}},"version":{{json .ServerVersion}},'
    '"default_runtime":{{json .DefaultRuntime}},"root":{{json .DockerRootDir}},'
    '"runtimes":{{json .Runtimes}}}'
)
# Deliberately omit environment variables, labels other than ownership, and logs.
CONTAINER_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
    '"owned":{{json (index .Config.Labels "io.reprolab.owned")}},'
    '"running":{{json .State.Running}},"paused":{{json .State.Paused}},'
    '"status":{{json .State.Status}},"started_at":{{json .State.StartedAt}},'
    '"restart_count":{{json .RestartCount}},"runtime":{{json .HostConfig.Runtime}},'
    '"privileged":{{json .HostConfig.Privileged}},"caps":{{json .HostConfig.CapAdd}},'
    '"pid_mode":{{json .HostConfig.PidMode}},"mounts":{{json .Mounts}}}'
)
PROTECTED_PATHS = [
    Path(value).resolve() for value in [
        '/root/.ssh', '/root/.git-credentials', '/root/.aws',
        '/etc/reprolab', '/opt/reprolab', '/opt/reprolab-runtime',
        '/var/lib/reprolab', '/var/lib/reprolab-caddy', '/etc/caddy/reprolab.Caddyfile',
        '/run/docker.sock', '/run/containerd', '/var/lib/docker', '/etc/docker',
        '/proc', '/sys', '/dev',
    ]
]


def command(arguments: list[str], *, seconds: float = 15) -> str:
    result = capture(arguments, seconds=seconds, output_limit=1024 * 1024)
    # Do not forward daemon/config-validation output: it can contain private data.
    require(result.exit_code == 0 and result.stop_reason is None,
            'Host check failed for ' + Path(arguments[0]).name + '; no private output was printed.')
    return result.stdout.decode()


def docker(*arguments: str) -> str:
    return command([DOCKER, '--host=unix:///var/run/docker.sock', *arguments])


def file_hash(path: Path) -> str | None:
    require(not path.is_symlink(), 'A managed configuration path is a symbolic link.')
    if not path.exists():
        return None
    require(path.is_file(), 'A managed configuration path is not a regular file.')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def containers() -> dict[str, dict]:
    identifiers = docker('ps', '--all', '--no-trunc', '--format', '{{.ID}}').splitlines()
    result = {}
    for identifier in identifiers:
        require(bool(re.fullmatch(r'[a-f0-9]{64}', identifier)), 'Invalid Docker container identifier.')
        item = json.loads(docker('inspect', identifier, '--format', CONTAINER_FORMAT))
        if item.get('owned') != 'true':
            result[identifier] = item
    return result


def docker_snapshot() -> dict:
    return {'daemon': json.loads(docker('info', '--format', INFO_FORMAT)),
            'containers': containers()}


def check_container(item: dict, *, docker_root: str | None = None) -> None:
    require(not item['privileged'], 'An existing container is privileged; use a separate VM for ReproLab.')
    dangerous = {'ALL', 'SYS_ADMIN', 'SYS_PTRACE', 'SYS_MODULE', 'SYS_RAWIO',
                 'DAC_READ_SEARCH', 'DAC_OVERRIDE'}
    capabilities = {value.removeprefix('CAP_') for value in item.get('caps') or []}
    require(not dangerous.intersection(capabilities),
            'An existing container has host-access capabilities; use a separate VM for ReproLab.')
    require(item.get('pid_mode') != 'host',
            'An existing container shares host processes; use a separate VM for ReproLab.')
    protected = PROTECTED_PATHS + ([Path(docker_root).resolve()] if docker_root else [])
    for mount in item.get('mounts') or []:
        if mount.get('Type') != 'bind':
            continue
        source = Path(mount['Source']).resolve()
        require(not any(source.is_relative_to(path) or path.is_relative_to(source)
                        for path in protected),
                'An existing container mounts host administration or ReproLab paths; use a separate VM.')
        require(source.name not in {'docker.sock', 'containerd.sock'},
                'An existing container controls the container daemon; use a separate VM.')


def verify_preserved(before: dict, after: dict) -> None:
    old_daemon, new_daemon = before['daemon'], after['daemon']
    for name in ['id', 'version', 'default_runtime', 'root']:
        require(old_daemon[name] == new_daemon[name],
                'The existing Docker installation or default runtime changed; setup stopped.')
    for name, runtime in old_daemon['runtimes'].items():
        require(new_daemon['runtimes'].get(name) == runtime,
                'An existing Docker runtime changed; setup stopped.')
    require(before['containers'] == after['containers'],
            'Existing containers changed identity, state or restart count; setup stopped.')


def listening_ports(raw: str) -> set[int]:
    ports = set()
    for line in raw.splitlines():
        fields = line.split()
        require(len(fields) >= 5, 'Could not parse the host listening ports.')
        endpoint = fields[3]
        port = endpoint.rsplit(':', 1)[-1]
        require(port.isdecimal(), 'Could not parse a host listening port.')
        ports.add(int(port))
    return ports


def service_state(name: str, verb: str) -> str:
    result = subprocess.run(['systemctl', verb, name], capture_output=True, text=True, timeout=10,
                            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
    return result.stdout.strip()


def check_host(args) -> None:
    memory = MEMINFO.read_text()
    available = int(re.search(r'^MemAvailable:\s+(\d+) kB$', memory, re.M).group(1))
    require(available >= 2 * 1024 ** 2,
            'ReproLab needs at least 2 GiB currently available RAM; resize this shared VM or use another.')
    tcp = listening_ports(command(['/usr/bin/ss', '-H', '-ltn']))
    udp = listening_ports(command(['/usr/bin/ss', '-H', '-lun']))
    conflict = tcp.intersection({80, 443, 8080})
    require(not conflict, 'Required TCP ports already in use: ' + ', '.join(map(str, sorted(conflict))))
    require(443 not in udp, 'UDP 443 is already in use by another service.')
    caddy_active = service_state('caddy.service', 'is-active')
    caddy_enabled = service_state('caddy.service', 'is-enabled')
    require(caddy_active in {'inactive', 'failed', 'unknown', ''}
            and caddy_enabled not in {'enabled', 'enabled-runtime', 'linked', 'linked-runtime'},
            'The existing Caddy service reserves HTTPS; preserve it and use a separate VM.')
    caddy_hash = file_hash(CADDY_CONFIG)
    require(CADDY_BINARY.exists() or caddy_hash is None,
            'An existing Caddy configuration would be activated by package installation; setup stopped.')
    own_caddy = HTTPS_CONFIG
    if own_caddy.exists():
        require(not own_caddy.is_symlink() and own_caddy.read_text().startswith('# ReproLab managed HTTPS\n'),
                'The ReproLab Caddyfile path contains an unrelated configuration.')
    snapshot = None
    if shutil.which('docker'):
        require(Path(DOCKER).is_file(), 'The fixed controller needs the existing Docker client at /usr/bin/docker.')
        require(service_state('docker.service', 'is-active') == 'active',
                'The existing Docker service is unavailable; it will not be restarted by this installer.')
        snapshot = docker_snapshot()
        require(args.shared_host or not snapshot['containers'],
                'Other project containers exist. Run with --shared-host to perform coexistence checks.')
        for item in snapshot['containers'].values():
            check_container(item, docker_root=snapshot['daemon']['root'])
    else:
        require(not args.shared_host, 'Shared-host setup requires the crawler Docker daemon to be running.')
    json_write(args.snapshot, {'docker': snapshot, 'caddy_hash': caddy_hash,
                              'caddy_active': caddy_active, 'caddy_enabled': caddy_enabled,
                              'shared_host': args.shared_host})
    print(json.dumps({'shared_host': args.shared_host, 'available_ram_mib': available // 1024,
                      'other_containers': len(snapshot['containers']) if snapshot else 0,
                      'required_ports': 'free', 'host_checks': 'passed'}))


def merged_runtime_config(existing: dict, binary: Path, live: dict) -> dict:
    require(isinstance(existing, dict), 'Docker configuration must be a JSON object.')
    proposed = copy.deepcopy(existing)
    runtimes = proposed.setdefault('runtimes', {})
    require(isinstance(runtimes, dict), 'Docker runtimes configuration is invalid.')
    expected = {'path': str(binary)}
    if 'runsc' in runtimes:
        require(isinstance(runtimes['runsc'], dict), 'Existing runsc configuration is invalid.')
        entry = dict(runtimes['runsc'])
        if entry.get('runtimeArgs') == []:
            entry.pop('runtimeArgs')
        require(entry == expected, 'A different runsc runtime is already configured; it will not be overwritten.')
    else:
        require('runsc' not in live['runtimes'],
                'An externally configured runsc runtime exists; it will not be overwritten.')
        runtimes['runsc'] = expected
    return proposed


def runtime_ready(snapshot: dict, binary: Path) -> bool:
    entry = snapshot['daemon']['runtimes'].get('runsc', {})
    return entry.get('path') == str(binary) and not entry.get('runtimeArgs')


def check_daemon_service() -> None:
    require(service_state('docker.service', 'is-active') == 'active', 'Docker must already be running.')
    reloadable = command(['systemctl', 'show', 'docker.service', '--property=CanReload', '--value']).strip()
    require(reloadable == 'yes', 'This Docker service cannot reload safely; no restart will be attempted.')
    pid = command(['systemctl', 'show', 'docker.service', '--property=MainPID', '--value']).strip()
    require(pid.isdecimal() and int(pid) > 0, 'Docker service PID is unavailable.')
    arguments = Path('/proc', pid, 'cmdline').read_bytes().decode().split('\0')
    require(Path(arguments[0]).name == 'dockerd', 'Docker is managed by an unsupported service wrapper.')
    for index, argument in enumerate(arguments):
        if argument == '--config-file':
            require(arguments[index + 1] == str(DAEMON_CONFIG), 'A custom Docker config path is in use.')
        elif argument.startswith('--config-file='):
            require(argument.split('=', 1)[1] == str(DAEMON_CONFIG), 'A custom Docker config path is in use.')
        require(not argument.startswith('--add-runtime'),
                'Docker runtime flags conflict with file-based configuration; no changes were made.')


def replace_config(path: Path, raw: bytes, metadata: os.stat_result | None) -> None:
    require(not path.is_symlink() and not path.parent.is_symlink(), 'Docker configuration path is a symlink.')
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reprolab-runtime-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
            if metadata:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def enable_runtime(args) -> None:
    require(os.geteuid() == 0, 'Runtime registration requires root.')
    require(args.binary == RUNTIME_BINARY and args.binary.is_file() and not args.binary.is_symlink(),
            'Use the pinned ReproLab runsc binary.')
    check_daemon_service()
    before = docker_snapshot()
    for item in before['containers'].values():
        check_container(item, docker_root=before['daemon']['root'])
    old_hash = file_hash(DAEMON_CONFIG)
    original = DAEMON_CONFIG.read_bytes() if old_hash is not None else None
    metadata = DAEMON_CONFIG.stat() if original is not None else None
    existing = json.loads(original) if original is not None else {}
    proposed = merged_runtime_config(existing, args.binary, before['daemon'])
    if proposed == existing and runtime_ready(before, args.binary):
        print('The pinned runsc runtime is already registered; Docker was not reloaded.')
        return
    raw = (json.dumps(proposed, indent=2) + '\n').encode()
    dockerd = shutil.which('dockerd')
    require(dockerd is not None, 'Docker daemon binary is unavailable for configuration validation.')
    with tempfile.TemporaryDirectory(prefix='reprolab-runtime-') as directory:
        candidate = Path(directory) / 'daemon.json'
        candidate.write_bytes(raw)
        candidate.chmod(0o600)
        command([dockerd, '--validate', '--config-file', str(candidate)])
    require(file_hash(DAEMON_CONFIG) == old_hash, 'Docker configuration changed during inspection; setup stopped.')
    backup = args.record.parent / ('docker-daemon-before-' + str(time.time_ns()) + '.json')
    private_write(backup, original if original is not None else b'{}\n')
    json_write(args.record, {'original_existed': original is not None, 'original_sha256': old_hash,
                             'backup': str(backup), 'runtime_binary': str(args.binary), 'state': 'applying'})
    replace_config(DAEMON_CONFIG, raw, metadata)
    try:
        command(['systemctl', 'reload', 'docker.service'])
        for attempt in range(10):
            after = docker_snapshot()
            verify_preserved(before, after)
            if runtime_ready(after, args.binary):
                break
            if attempt < 9:
                time.sleep(1)
        require(runtime_ready(after, args.binary), 'Docker did not activate the pinned runsc runtime.')
    except Exception:
        # Restore only our exact change; never overwrite a concurrent operator edit.
        expected_hash = hashlib.sha256(raw).hexdigest()
        if file_hash(DAEMON_CONFIG) != expected_hash:
            raise RuntimeError('Docker config changed concurrently; restore from the private backup before continuing.') from None
        if original is None:
            DAEMON_CONFIG.unlink()
        else:
            replace_config(DAEMON_CONFIG, original, metadata)
        try:
            command(['systemctl', 'reload', 'docker.service'])
            verify_preserved(before, docker_snapshot())
        except Exception:
            raise RuntimeError('Original Docker config was restored, but runtime/container recovery is unconfirmed; no restart was attempted.') from None
        json_write(args.record, {'state': 'rolled-back', 'backup': str(backup),
                                 'original_existed': original is not None})
        raise RuntimeError('Runtime activation failed. Original Docker config and existing containers were preserved.') from None
    json_write(args.record, {'state': 'complete', 'backup': str(backup),
                             'original_existed': original is not None,
                             'original_sha256': old_hash, 'runtime_binary': str(args.binary)})
    print('Pinned runsc added by reload; existing Docker version, default runtime and containers preserved.')


def verify_host(args) -> None:
    before = json.loads(args.snapshot.read_text())
    after = docker_snapshot()
    if before['docker'] is not None:
        verify_preserved(before['docker'], after)
    else:
        require(not after['containers'], 'An unrelated container appeared during setup; check the host.')
    if before['caddy_hash'] is not None:
        require(file_hash(CADDY_CONFIG) == before['caddy_hash'],
                'The existing host Caddy configuration changed during setup.')
        require(service_state('caddy.service', 'is-active') == before['caddy_active']
                and service_state('caddy.service', 'is-enabled') == before['caddy_enabled'],
                'The existing host Caddy service state changed during setup.')
    json_write(args.output, {'existing_containers_preserved': True, 'docker_default_runtime': after['daemon']['default_runtime'],
                             'other_container_count': len(after['containers']), 'shared_host': before['shared_host'],
                             'checked_at_unix': int(time.time())})
    print('Existing containers and host Caddy configuration passed the final preservation check.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check')
    check.add_argument('--shared-host', action='store_true')
    check.add_argument('--snapshot', type=Path, default=Path('/etc/reprolab/host-before.json'))
    runtime = commands.add_parser('enable-runtime')
    runtime.add_argument('--binary', type=Path, default=RUNTIME_BINARY)
    runtime.add_argument('--record', type=Path, default=Path('/etc/reprolab/runtime-change.json'))
    verify = commands.add_parser('verify')
    verify.add_argument('--snapshot', type=Path, default=Path('/etc/reprolab/host-before.json'))
    verify.add_argument('--output', type=Path, default=Path('reports/host-preservation.json'))
    args = parser.parse_args()
    {'check': check_host, 'enable-runtime': enable_runtime, 'verify': verify_host}[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ReproLab host setup stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
