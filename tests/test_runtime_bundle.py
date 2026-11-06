import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest

from reprolab.runtime_bundle import digest, install_bundle


PAYLOADS = [('runsc', b'test-only-runsc', 0o755),
            ('gvisor-bin/gvisor_sentry', b'test-only-sentry', 0o755),
            ('gvisor-bin/checkpointgofer', b'test-only-gofer', 0o755)]


def archive(root, entries=PAYLOADS):
    path = root/'bundle.tar.bz2'
    with tarfile.open(path, 'w:bz2') as output:
        for entry in entries:
            name, raw, mode = entry[:3]
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = entry[3] if len(entry) > 3 else tarfile.REGTYPE
            member.linkname = entry[4] if len(entry) > 4 else ''
            member.size = len(raw) if member.isfile() else 0
            output.addfile(member, io.BytesIO(raw) if member.isfile() else None)
    return path


def install(source, destination):
    return install_bundle(source, destination, expected_sha256=digest(source))


class RuntimeBundleTests(unittest.TestCase):
    def test_complete_bundle_repairs_runsc_only_installation_and_rerun_keeps_existing_binaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = archive(root)
            destination = root/'runtime'
            destination.mkdir()
            runsc = destination/'runsc'
            runsc.write_bytes(PAYLOADS[0][1])
            inode = runsc.stat().st_ino
            unrelated = destination/'operator-note.txt'
            unrelated.write_text('keep this')
            result = install(source, destination)
            self.assertEqual(set(result['installed']),
                             {'gvisor-bin/gvisor_sentry', 'gvisor-bin/checkpointgofer'})
            for name, raw, _ in PAYLOADS:
                self.assertEqual((destination/name).read_bytes(), raw)
            self.assertEqual(runsc.stat().st_ino, inode)
            self.assertEqual(unrelated.read_text(), 'keep this')
            self.assertEqual(install(source, destination)['installed'], [])
            self.assertEqual(runsc.stat().st_ino, inode)

    def test_missing_sentry_is_rejected_before_creating_a_runtime_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = archive(root, PAYLOADS[:1])
            destination = root/'runtime'
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                install(source, destination)
            self.assertFalse(destination.exists())

    def test_wrong_archive_checksum_cannot_modify_existing_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = archive(root)
            destination = root/'runtime'
            destination.mkdir()
            existing = destination/'runsc'
            existing.write_bytes(b'keep-existing')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                install_bundle(source, destination, expected_sha256='0'*64)
            self.assertEqual(existing.read_bytes(), b'keep-existing')
            self.assertFalse((destination/'gvisor-bin').exists())

    def test_unsafe_archive_paths_links_devices_duplicates_and_file_directory_conflicts_are_rejected(self):
        variants = [
            [('../outside', b'bad', 0o644)],
            [('/absolute', b'bad', 0o644)],
            [('link', b'', 0o755, tarfile.SYMTYPE, '/tmp/outside')],
            [('link', b'', 0o755, tarfile.LNKTYPE, 'runsc')],
            [('device', b'', 0o644, tarfile.CHRTYPE)],
            [('runsc', b'duplicate', 0o755)],
            [('runsc/nested', b'bad', 0o644)],
        ]
        for extra in variants:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = archive(root, PAYLOADS + extra)
                destination = root/'runtime'
                with self.assertRaises(RuntimeError):
                    install(source, destination)
                self.assertFalse(destination.exists())

    def test_conflicting_sidecar_stops_before_any_new_destination_files_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = archive(root)
            destination = root/'runtime'
            (destination/'gvisor-bin').mkdir(parents=True)
            existing = destination/'gvisor-bin/gvisor_sentry'
            existing.write_bytes(b'keep-conflicting-sidecar')
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                install(source, destination)
            self.assertEqual(existing.read_bytes(), b'keep-conflicting-sidecar')
            self.assertFalse((destination/'runsc').exists())
            self.assertFalse((destination/'gvisor-bin/checkpointgofer').exists())

    def test_symlink_destinations_and_members_are_never_followed(self):
        for member in [False, True]:
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = archive(root)
                destination = root/'runtime'
                outside = root/'outside'
                outside.mkdir()
                marker = outside/'runsc'
                marker.write_bytes(PAYLOADS[0][1])
                if member:
                    destination.mkdir()
                    (destination/'runsc').symlink_to(marker)
                else:
                    destination.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(RuntimeError, 'symbolic link'):
                    install(source, destination)
                self.assertEqual(marker.read_bytes(), PAYLOADS[0][1])
                self.assertFalse((outside/'gvisor-bin').exists())

    def test_bundle_permissions_strip_special_bits_without_exposing_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            source = archive(root, [(name, raw, mode | 0o4000) for name, raw, mode in PAYLOADS]
                             + [('notice.txt', b'test-only-license', 0o600)])
            destination = root/'runtime'
            install(source, destination)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((destination/'gvisor-bin').stat().st_mode), 0o755)
            for name, _, _ in PAYLOADS:
                self.assertEqual(stat.S_IMODE((destination/name).stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((destination/'notice.txt').stat().st_mode), 0o644)


if __name__ == '__main__':
    unittest.main()
