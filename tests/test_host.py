import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from reprolab import host
from reprolab.sandbox import Captured


def crawler():
    return {'id': 'a' * 64, 'name': '/optimai', 'image': 'sha256:' + 'b' * 64,
            'owned': None, 'running': True, 'paused': False, 'status': 'running',
            'started_at': '2026-11-05T00:00:00Z', 'restart_count': 0,
            'runtime': 'runc', 'privileged': False, 'caps': None, 'pid_mode': '',
            'mounts': [{'Type': 'bind', 'Source': '/srv/optimai/data', 'RW': True}]}


def snapshot():
    return {'daemon': {'id': 'existing-daemon', 'version': '28.5.0',
                       'default_runtime': 'runc', 'root': '/var/lib/docker',
                       'runtimes': {'runc': {'path': 'runc'}}},
            'containers': {'a' * 64: crawler()}}


class SharedHostTests(unittest.TestCase):
    def test_ordinary_crawler_is_allowed_but_host_control_and_private_mounts_are_rejected(self):
        host.check_container(crawler())
        host.check_container(crawler() | {'mounts': [
            {'Type': 'bind', 'Source': '/root/.optimai', 'RW': True}]})
        mutations = [
            {'privileged': True}, {'caps': ['CAP_SYS_ADMIN']}, {'pid_mode': 'host'},
            *[{'mounts': [{'Type': 'bind', 'Source': path, 'RW': False}]} for path in
              ['/', '/root', '/etc', '/var/run/docker.sock', '/run',
               '/etc/reprolab', '/var/lib/reprolab/work', '/opt/reprolab-runtime',
               '/mnt/custom-docker']],
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                host.check_container(crawler() | mutation, docker_root='/mnt/custom-docker')
        # Docker-managed data volumes are not access to Docker administration.
        host.check_container(crawler() | {'mounts': [
            {'Type': 'volume', 'Source': '/var/lib/docker/volumes/optimai/_data'}]})

    def test_runtime_merge_preserves_default_and_all_unrelated_configuration(self):
        existing = {'default-runtime': 'custom',
                    'runtimes': {'custom': {'path': '/usr/bin/custom', 'runtimeArgs': ['--flag']}},
                    'live-restore': True, 'log-driver': 'local',
                    'proxies': {'https-proxy': 'https://test-only-private-value'}}
        original = copy.deepcopy(existing)
        proposed = host.merged_runtime_config(existing, host.RUNTIME_BINARY, snapshot()['daemon'])
        self.assertEqual(existing, original)
        self.assertEqual(proposed.pop('runtimes')['custom'], existing['runtimes']['custom'])
        self.assertEqual(proposed, {name: value for name, value in existing.items() if name != 'runtimes'})
        for conflict in [{'runsc': {'path': '/usr/local/bin/runsc'}},
                         {'runsc': {'path': str(host.RUNTIME_BINARY), 'runtimeArgs': ['--network=host']}}]:
            with self.subTest(conflict=conflict), self.assertRaises(RuntimeError):
                host.merged_runtime_config({'runtimes': conflict}, host.RUNTIME_BINARY, snapshot()['daemon'])
        live = snapshot()['daemon']
        live['runtimes']['runsc'] = {'path': '/another/runsc'}
        with self.assertRaises(RuntimeError):
            host.merged_runtime_config({}, host.RUNTIME_BINARY, live)

    def test_preservation_detects_restarted_removed_added_containers_and_runtime_changes(self):
        before = snapshot()
        after = copy.deepcopy(before)
        after['daemon']['runtimes']['runsc'] = {'path': str(host.RUNTIME_BINARY)}
        host.verify_preserved(before, after)
        for mutation in ['restart', 'removed', 'added', 'default-runtime', 'existing-runtime', 'version']:
            changed = copy.deepcopy(after)
            if mutation == 'restart':
                changed['containers']['a' * 64]['restart_count'] = 1
            elif mutation == 'removed':
                changed['containers'].clear()
            elif mutation == 'added':
                changed['containers']['c' * 64] = crawler() | {'id': 'c' * 64}
            elif mutation == 'default-runtime':
                changed['daemon']['default_runtime'] = 'runsc'
            elif mutation == 'existing-runtime':
                changed['daemon']['runtimes']['runc']['path'] = '/changed/runc'
            else:
                changed['daemon']['version'] = '29.0.0'
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                host.verify_preserved(before, changed)

    def test_ipv4_ipv6_and_wildcard_ports_are_detected(self):
        self.assertEqual(host.listening_ports(
            'LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*\n'
            'LISTEN 0 128 [::]:443 [::]:*\n'
            'LISTEN 0 128 *:80 *:*\n'), {80, 443, 8080})
        with self.assertRaises(RuntimeError):
            host.listening_ports('unparseable')

    def test_host_conflicts_stop_before_writing_a_snapshot_or_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / 'meminfo'
            output = root / 'before.json'
            args = argparse.Namespace(shared_host=True, snapshot=output)
            with patch.object(host, 'MEMINFO', memory), patch.object(host, 'CADDY_CONFIG', root/'Caddyfile'), \
                 patch.object(host, 'HTTPS_CONFIG', root/'reprolab.Caddyfile'), \
                 patch.object(host, 'CADDY_BINARY', root/'caddy'), \
                 patch.object(host, 'DOCKER', '/usr/bin/python3'), \
                 patch.object(host, 'shutil') as sh, \
                 patch.object(host, 'docker_snapshot', return_value=snapshot()), \
                 patch.object(host, 'service_state', side_effect=lambda name, verb: 'active' if name == 'docker.service'
                              else 'inactive' if verb == 'is-active' else 'disabled'), \
                 patch.object(host, 'command', return_value='') as run:
                sh.which.return_value = '/usr/bin/python3'
                memory.write_text('MemAvailable: 1048576 kB\n')
                with self.assertRaises(RuntimeError): host.check_host(args)
                self.assertFalse(output.exists())
                run.assert_not_called()
                memory.write_text('MemAvailable: 3145728 kB\n')
                run.side_effect = ['LISTEN 0 128 0.0.0.0:80 0.0.0.0:*\n', '']
                with self.assertRaises(RuntimeError): host.check_host(args)
                self.assertFalse(output.exists())
                run.side_effect = None
                with contextlib.redirect_stdout(io.StringIO()):
                    host.check_host(args)
                saved = json.loads(output.read_text())
                self.assertEqual(saved['docker']['containers'], snapshot()['containers'])
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_reload_failure_restores_exact_config_and_permissions_without_restart(self):
        for existed in [False, True]:
            with self.subTest(existed=existed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config, binary = root/'daemon.json', root/'runsc'
                binary.write_bytes(b'test-only-runtime')
                original = b'{ "live-restore": true, "log-driver": "local" }\n'
                if existed:
                    config.write_bytes(original)
                    config.chmod(0o640)
                args = argparse.Namespace(binary=binary, record=root/'runtime-change.json')
                calls = []

                def run(arguments, **kwargs):
                    calls.append(arguments)
                    if arguments == ['systemctl', 'reload', 'docker.service'] and \
                            sum(call == arguments for call in calls) == 1:
                        raise RuntimeError('test-only-reload-failure')
                    return ''

                with patch.object(host, 'DAEMON_CONFIG', config), patch.object(host, 'RUNTIME_BINARY', binary), \
                     patch.object(host.os, 'geteuid', return_value=0), \
                     patch.object(host, 'check_daemon_service'), \
                     patch.object(host, 'docker_snapshot', side_effect=[snapshot(), snapshot()]), \
                     patch.object(host, 'command', side_effect=run), \
                     patch.object(host.shutil, 'which', return_value='/usr/bin/dockerd'):
                    with self.assertRaisesRegex(RuntimeError, 'Original Docker config'):
                        host.enable_runtime(args)
                if existed:
                    self.assertEqual(config.read_bytes(), original)
                    self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
                else:
                    self.assertFalse(config.exists())
                self.assertFalse(any('restart' in command or 'stop' in command for command in calls))
                self.assertEqual(json.loads(args.record.read_text())['state'], 'rolled-back')

    def test_success_adds_only_runsc_and_rerun_does_not_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, binary = root/'daemon.json', root/'runsc'
            binary.write_bytes(b'test-only-runtime')
            existing = {'log-driver': 'local', 'default-runtime': 'runc'}
            config.write_text(json.dumps(existing))
            config.chmod(0o640)
            before, after = snapshot(), snapshot()
            after['daemon']['runtimes']['runsc'] = {'path': str(binary)}
            args = argparse.Namespace(binary=binary, record=root/'change.json')
            with patch.object(host, 'DAEMON_CONFIG', config), patch.object(host, 'RUNTIME_BINARY', binary), \
                 patch.object(host.os, 'geteuid', return_value=0), \
                 patch.object(host, 'check_daemon_service'), \
                 patch.object(host, 'docker_snapshot', side_effect=[before, after, after]), \
                 patch.object(host, 'command', return_value='') as run, \
                 patch.object(host.shutil, 'which', return_value='/usr/bin/dockerd'), \
                 contextlib.redirect_stdout(io.StringIO()):
                host.enable_runtime(args)
                self.assertEqual(json.loads(config.read_text()),
                                 existing | {'runtimes': {'runsc': {'path': str(binary)}}})
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
                run.reset_mock()
                host.enable_runtime(args)
                run.assert_not_called()

    def test_concurrent_configuration_edit_is_not_overwritten_during_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, binary = root/'daemon.json', root/'runsc'
            binary.write_bytes(b'test-only-runtime')
            config.write_text('{}\n')
            concurrent = b'{"operator-change":true}\n'
            args = argparse.Namespace(binary=binary, record=root/'change.json')

            def run(arguments, **kwargs):
                if arguments == ['systemctl', 'reload', 'docker.service']:
                    config.write_bytes(concurrent)
                    raise RuntimeError('test-only-reload-failure')
                return ''

            with patch.object(host, 'DAEMON_CONFIG', config), patch.object(host, 'RUNTIME_BINARY', binary), \
                 patch.object(host.os, 'geteuid', return_value=0), \
                 patch.object(host, 'check_daemon_service'), \
                 patch.object(host, 'docker_snapshot', return_value=snapshot()), \
                 patch.object(host, 'command', side_effect=run), \
                 patch.object(host.shutil, 'which', return_value='/usr/bin/dockerd'):
                with self.assertRaisesRegex(RuntimeError, 'changed concurrently'):
                    host.enable_runtime(args)
            self.assertEqual(config.read_bytes(), concurrent)

    def test_private_daemon_output_is_not_forwarded_on_failure(self):
        with patch.object(host, 'capture', return_value=Captured(
                b'test-only-private-value', b'test-only-private-value', 1, None, 10)):
            with self.assertRaises(RuntimeError) as raised:
                host.command(['/usr/bin/dockerd', '--validate'])
        self.assertNotIn('test-only-private-value', str(raised.exception))

    def test_runtime_registration_rejects_unprivileged_execution_before_host_changes(self):
        with patch.object(host.os, 'geteuid', return_value=1000), \
             patch.object(host, 'docker_snapshot') as inspect:
            with self.assertRaisesRegex(RuntimeError, 'requires root'):
                host.enable_runtime(argparse.Namespace(binary=host.RUNTIME_BINARY))
        inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
