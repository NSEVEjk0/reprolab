import argparse
import contextlib
import getpass
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest
import urllib.error
import warnings
from unittest.mock import Mock, patch

from reprolab.deploy import (INFERENCE_HEALTH_URL, browser, check_inference_health,
                             configure, prompt_inference_key, read_env, replace_key,
                             target, validate_provider)


class DeploymentTests(unittest.TestCase):
    def test_public_target_cannot_inject_shell_or_caddy_directives(self):
        self.assertEqual(target('8.8.8.8', None), ('8.8.8.8', '8.8.8.8.sslip.io'))
        for address, hostname in [('127.0.0.1', None), ('10.0.0.1', None),
                                  ('8.8.8.8', 'example.com { reverse_proxy evil }'),
                                  ('8.8.8.8', 'https://example.com'),
                                  ('8.8.8.8', 'example.com; touch /tmp/file')]:
            with self.subTest(address=address, hostname=hostname), self.assertRaises((ValueError, RuntimeError)):
                target(address, hostname)

    def test_environment_values_are_data_and_never_shell_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.env'
            marker = Path(directory) / 'executed'
            malicious = '$(touch ' + str(marker) + ')'
            path.write_text('VULTR_INFERENCE_API_KEY=' + json.dumps(malicious) + '\n')
            path.chmod(0o600)
            self.assertEqual(read_env(path)['VULTR_INFERENCE_API_KEY'], malicious)
            self.assertFalse(marker.exists())

    def test_publicly_readable_secret_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.env'
            path.write_text('VULTR_INFERENCE_API_KEY="test-only-credential"\n')
            path.chmod(0o644)
            with self.assertRaises(RuntimeError): read_env(path)

    def test_missing_model_cannot_be_treated_as_inference_ready(self):
        with patch('reprolab.deploy.check_inference_health') as health, \
             patch('reprolab.deploy.fetch_json', return_value={'data': [{'id': 'different-model'}]}):
            with self.assertRaisesRegex(RuntimeError, 'absent from the current Vultr'):
                validate_provider('test-only-credential', 'glm-5.3-flash')
        health.assert_called_once_with('test-only-credential')

    def test_authenticated_health_accepts_plaintext_without_generating_a_completion(self):
        response = io.BytesIO(b'OK')
        response.status = 200
        with patch('reprolab.deploy.urllib.request.urlopen', return_value=response) as request:
            check_inference_health('test-only-credential')
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, INFERENCE_HEALTH_URL)
        self.assertEqual(sent.get_method(), 'GET')
        self.assertEqual(sent.get_header('Authorization'), 'Bearer test-only-credential')
        self.assertEqual(request.call_args.kwargs['timeout'], 12)

    def test_public_model_listing_cannot_validate_a_rejected_key(self):
        error = urllib.error.HTTPError(INFERENCE_HEALTH_URL, 401,
                                      'test-only-credential', {}, io.BytesIO(b'private response'))
        with patch('reprolab.deploy.urllib.request.urlopen', side_effect=error), \
             patch('reprolab.deploy.fetch_json', return_value={'data': [{'id': 'glm-5.3-flash'}]}) as models:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as raised:
                validate_provider('test-only-credential', 'glm-5.3-flash')
        models.assert_not_called()
        self.assertNotIn('test-only-credential', str(raised.exception))
        self.assertNotIn('private response', str(raised.exception))

    def test_health_rejects_unexpected_status_and_oversized_responses(self):
        for status, raw in [(202, b'Pending'), (200, b'x' * (64 * 1024 + 1))]:
            with self.subTest(status=status, size=len(raw)):
                response = io.BytesIO(raw)
                response.status = status
                with patch('reprolab.deploy.urllib.request.urlopen', return_value=response):
                    with self.assertRaises(RuntimeError):
                        check_inference_health('test-only-credential')

    def test_health_network_errors_do_not_print_request_details(self):
        for error in [urllib.error.URLError('test-only-credential'), TimeoutError('test-only-credential')]:
            with self.subTest(error=type(error).__name__):
                with patch('reprolab.deploy.urllib.request.urlopen', side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, 'deadline') as raised:
                        check_inference_health('test-only-credential')
                self.assertNotIn('test-only-credential', str(raised.exception))

    def test_key_prompt_stops_before_getpass_can_fall_back_to_echoed_input(self):
        fallback = []
        def unavailable(prompt):
            warnings.warn('Hidden input unavailable', getpass.GetPassWarning)
            fallback.append(True)
            return 'test-only-credential'
        with patch('reprolab.deploy.getpass.getpass', side_effect=unavailable):
            with self.assertRaisesRegex(RuntimeError, 'interactive VPS terminal'):
                prompt_inference_key()
        self.assertEqual(fallback, [])

    def test_replacement_auth_failure_preserves_existing_configuration_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reprolab.env'
            raw = (b'# Operator settings\nVULTR_INFERENCE_API_KEY="test-only-old-key"\n'
                   b'VULTR_INFERENCE_MODEL="glm-5.3-flash"\nREPROLAB_DAILY_JOB_LIMIT="8"\n')
            path.write_bytes(raw)
            path.chmod(0o600)
            error = urllib.error.HTTPError(INFERENCE_HEALTH_URL, 401, 'Unauthorized', {}, None)
            with patch('reprolab.deploy.getpass.getpass', return_value='test-only-replacement'), \
                 patch('reprolab.deploy.urllib.request.urlopen', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                    replace_key(argparse.Namespace(config=path))
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_validated_replacement_preserves_other_settings_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reprolab.env'
            values = {'VULTR_INFERENCE_API_KEY': 'test-only-old-key',
                      'VULTR_INFERENCE_MODEL': 'glm-5.3-flash',
                      'REPROLAB_ACCESS_CODE': 'test-only-access-code',
                      'REPROLAB_DAILY_JOB_LIMIT': '8', 'OPERATOR_SETTING': 'keep this value'}
            path.write_text(''.join(name + '=' + json.dumps(value) + '\n' for name, value in values.items()))
            path.chmod(0o600)
            output = io.StringIO()
            with patch('reprolab.deploy.getpass.getpass', return_value='test-only-replacement'), \
                 patch('reprolab.deploy.validate_provider') as validate, \
                 contextlib.redirect_stdout(output):
                replace_key(argparse.Namespace(config=path))
            validate.assert_called_once_with('test-only-replacement', 'glm-5.3-flash')
            values['VULTR_INFERENCE_API_KEY'] = 'test-only-replacement'
            self.assertEqual(read_env(path), values)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for value in ['test-only-old-key', 'test-only-replacement', 'test-only-access-code']:
                self.assertNotIn(value, output.getvalue())

    def test_rerunning_configuration_preserves_credentials_and_writes_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(config=root/'reprolab.env', state=root/'state',
                                      ip='8.8.8.8', hostname=None, commit='a'*40)
            with patch('reprolab.deploy.getpass.getpass', return_value='test-only-credential') as prompt, \
                 patch('reprolab.deploy.validate_provider') as lookup:
                configure(args)
                first = read_env(args.config)
                configure(args)
                self.assertEqual(prompt.call_count, 1)
                self.assertEqual(lookup.call_count, 2)
                self.assertEqual(first, read_env(args.config))
            for name in ['reprolab.env', 'demo-access.txt', 'deployment.json']:
                self.assertEqual(stat.S_IMODE((root/name).stat().st_mode), 0o600)
            self.assertEqual(first['REPROLAB_PUBLIC_JOBS'], 'false')
            self.assertEqual(first['REPROLAB_DAILY_JOB_LIMIT'], '8')
            self.assertNotIn('test-only-credential', (root/'deployment.json').read_text())

    def test_failed_provider_auth_does_not_replace_existing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'reprolab.env'
            raw = b'VULTR_INFERENCE_API_KEY="test-only-credential"\n'
            path.write_bytes(raw); path.chmod(0o600)
            args = argparse.Namespace(config=path, state=root/'state', ip='8.8.8.8',
                                      hostname=None, commit='a'*40)
            with patch('reprolab.deploy.validate_provider', side_effect=RuntimeError('Unauthorized')):
                with self.assertRaises(RuntimeError): configure(args)
            self.assertEqual(path.read_bytes(), raw)

    def test_browser_never_receives_provider_key_and_failure_output_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'reprolab.env'
            values = {'VULTR_INFERENCE_API_KEY': 'provider-test-only-secret',
                      'REPROLAB_ACCESS_CODE': 'access-test-only-secret',
                      'REPROLAB_ORIGIN': 'https://8.8.8.8.sslip.io'}
            path.write_text(''.join(name+'='+json.dumps(value)+'\n' for name,value in values.items()))
            path.chmod(0o600)
            result = subprocess.CompletedProcess(['npm'], 1, 'provider-test-only-secret access-test-only-secret', '')
            output = io.StringIO()
            with patch.dict(os.environ, {'VULTR_INFERENCE_API_KEY': values['VULTR_INFERENCE_API_KEY']}), \
                 patch('reprolab.deploy.subprocess.run', return_value=result) as run, \
                 contextlib.redirect_stdout(output):
                with self.assertRaises(RuntimeError): browser(argparse.Namespace(config=path,npm='/test/npm'))
            self.assertNotIn('VULTR_INFERENCE_API_KEY', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['env']['REPROLAB_VERIFY_ACCESS'], values['REPROLAB_ACCESS_CODE'])
            for name in ['VULTR_INFERENCE_API_KEY', 'REPROLAB_ACCESS_CODE']:
                self.assertNotIn(values[name], output.getvalue())


class RecoveryScriptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'reprolab.env'
        self.original = (b'VULTR_INFERENCE_API_KEY="test-only-old-key"\n'
                         b'VULTR_INFERENCE_MODEL="glm-5.3-flash"\n'
                         b'REPROLAB_ORIGIN="https://8.8.8.8.sslip.io"\n'
                         b'REPROLAB_ACCESS_CODE="test-only-access-code"\n'
                         b'REPROLAB_DAILY_JOB_LIMIT="8"\n')
        self.config.write_bytes(self.original)
        self.config.chmod(0o600)
        self.status = {'state': 'failed', 'phase': 'one-real-vultr-repair', 'source_commit': 'b' * 40}
        self.metadata = {'provider': 'Vultr', 'source_commit': 'b' * 40, 'ip': '8.8.8.8',
                         'hostname': '8.8.8.8.sslip.io', 'origin': 'https://8.8.8.8.sslip.io'}
        script = Path(__file__).resolve().parents[1] / 'infra/recover-vultr.sh'
        blocks = re.findall(r"python3 - <<'PY'(?: \|\| true)?\n(.*?)\nPY(?:\n|$)", script.read_text(), re.S)
        key_source = next(block for block in blocks if 'def replace_deployed_key():' in block)
        namespace = {'__name__': 'reprolab_recovery_test'}
        exec(compile(key_source, str(script), 'exec'), namespace)
        namespace['CONFIG'] = self.config
        # The existing deployment's model-only validator would accept any key.
        self.models = Mock()
        namespace['validate_provider'] = self.models
        self.replace = namespace['replace_deployed_key']
        original_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            if str(path) == '/etc/reprolab/install-status.json':
                return json.dumps(self.status)
            if str(path) == '/etc/reprolab/deployment.json':
                return json.dumps(self.metadata)
            return original_read_text(path, *args, **kwargs)
        self.enterContext(patch.object(Path, 'read_text', new=read_text))
        self.prompt = self.enterContext(patch('getpass.getpass', return_value='test-only-replacement'))
        self.request = self.enterContext(patch('urllib.request.urlopen'))

    def test_incomplete_or_mismatched_deployment_stops_before_prompt_or_network(self):
        for changes in [('phase', 'pinned-gvisor'), ('source_commit', 'c' * 40), ('state', 'complete')]:
            with self.subTest(changes=changes):
                original = self.status.copy()
                self.status[changes[0]] = changes[1]
                with self.assertRaises(RuntimeError):
                    self.replace()
                self.status = original
        self.prompt.assert_not_called()
        self.request.assert_not_called()
        self.models.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_legacy_recovery_rejects_unauthorized_key_before_writing(self):
        self.request.side_effect = urllib.error.HTTPError(INFERENCE_HEALTH_URL, 401, 'Unauthorized', {}, None)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            self.replace()
        self.models.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_legacy_recovery_validates_health_and_preserves_all_other_settings(self):
        response = io.BytesIO(b'OK')
        response.status = 200
        self.request.return_value = response
        before = read_env(self.config)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.replace()
        sent = self.request.call_args.args[0]
        self.assertEqual(sent.full_url, INFERENCE_HEALTH_URL)
        self.assertEqual(sent.get_header('Authorization'), 'Bearer test-only-replacement')
        self.models.assert_called_once_with('test-only-replacement', 'glm-5.3-flash')
        before['VULTR_INFERENCE_API_KEY'] = 'test-only-replacement'
        self.assertEqual(read_env(self.config), before)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertNotIn('test-only-replacement', output.getvalue())

    def test_legacy_recovery_requires_hidden_input(self):
        def unavailable(prompt):
            warnings.warn('Hidden input unavailable', getpass.GetPassWarning)
            raise AssertionError('Echo fallback was reached')
        self.prompt.side_effect = unavailable
        with self.assertRaisesRegex(RuntimeError, 'Hidden input is unavailable'):
            self.replace()
        self.request.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
