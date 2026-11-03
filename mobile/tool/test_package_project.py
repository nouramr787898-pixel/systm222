import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('package_project',Path(__file__).with_name('package_project.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class PackageProjectTests(unittest.TestCase):
    def test_complete_single_folder_preserves_wrapper_and_excludes_secrets_caches_and_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';root.mkdir();output=Path(temp)/'delivery.zip'
            for name in module.REQUIRED:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('source')
            for name in ['.env','android/key.properties','android/upload.jks','ios/APNs.p8','build/cache.bin','.dart_tool/cache.json','data.sqlite3']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('DO NOT SHIP')
            module.package(root,output)
            with zipfile.ZipFile(output) as archive:
                names=archive.namelist()
                self.assertTrue(all(name.startswith('ehkam_mobile/') for name in names))
                self.assertIn('ehkam_mobile/android/gradlew',names)
                self.assertIn('ehkam_mobile/ios/Runner/Info.plist',names)
                self.assertTrue(archive.getinfo('ehkam_mobile/android/gradlew').external_attr>>16&0o111)
                self.assertFalse(any(archive.read(name)==b'DO NOT SHIP' for name in names))
            self.assertTrue(output.with_suffix('.sha256').is_file())

    def test_incomplete_source_and_recursive_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaisesRegex(ValueError,'Incomplete Flutter source'):module.package(root,root.parent/'out.zip')
            for name in module.REQUIRED:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            with self.assertRaisesRegex(ValueError,'outside the Flutter folder'):module.package(root,root/'out.zip')


if __name__=='__main__':unittest.main()
