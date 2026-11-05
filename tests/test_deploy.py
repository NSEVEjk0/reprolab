import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from reprolab.deploy import browser, configure, read_env, target, validate_provider


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
        with patch('reprolab.deploy.fetch_json', return_value={'data': [{'id': 'different-model'}]}):
            with self.assertRaises(RuntimeError): validate_provider('test-only-credential', 'glm-5.3-flash')

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


if __name__ == '__main__':
    unittest.main()
