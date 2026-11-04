"""Validate the host configurator on official-template-shaped disposable files."""
import importlib.util
import plistlib
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
spec=importlib.util.spec_from_file_location('bootstrap',Path(__file__).with_name('bootstrap.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class BootstrapTests(unittest.TestCase):
    def test_foreground_permissions_keychain_and_release_signing_are_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            manifest=root/'android/app/src/main/AndroidManifest.xml';manifest.parent.mkdir(parents=True)
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:label="Demo"/></manifest>')
            (root/'android/settings.gradle.kts').write_text('plugins { id("com.android.application") version "8.11.1" apply false }')
            gradle=root/'android/app/build.gradle.kts'
            gradle.write_text('plugins { id("com.android.application") }\nandroid {\n    namespace = "com.demo.app"\n    defaultConfig { applicationId = "com.demo.app"; minSdk = flutter.minSdkVersion }\n    buildTypes { release { signingConfig = signingConfigs.getByName("debug") } }\n}\n')
            source=root/'android/app/src/main/kotlin/com/demo/app/MainActivity.kt';source.parent.mkdir(parents=True);source.write_text('package com.demo.app\nclass MainActivity')
            info=root/'ios/Runner/Info.plist';info.parent.mkdir(parents=True)
            with info.open('wb') as f:plistlib.dump({'CFBundleDisplayName':'Demo'},f)
            pbx=root/'ios/Runner.xcodeproj/project.pbxproj';pbx.parent.mkdir(parents=True)
            pbx.write_text('\t\t\tbuildSettings = {\n\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 13.0;\n\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.demo.app;\n\t\t\t};\n\t\t\tbuildSettings = {\n\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.demo.app.RunnerTests;\n\t\t\t};')
            module.configure(root,'com.ehkam.portal','com.ehkam.portal');module.configure(root,'com.ehkam.portal','com.ehkam.portal')
            tree=ET.parse(manifest);permissions=[p.get('{'+module.ANDROID+'}name') for p in tree.getroot().findall('uses-permission')]
            self.assertEqual(permissions.count('android.permission.ACCESS_FINE_LOCATION'),1)
            self.assertNotIn('android.permission.ACCESS_BACKGROUND_LOCATION',permissions)
            self.assertEqual(permissions.count('android.permission.POST_NOTIFICATIONS'),1)
            self.assertIn('IPHONEOS_DEPLOYMENT_TARGET = 15.0;',pbx.read_text())
            self.assertEqual(tree.getroot().find('application').get('{'+module.ANDROID+'}allowBackup'),'false')
            self.assertLess(gradle.read_text().index('plugins {'),gradle.read_text().index('val ehkamSigning'))
            self.assertEqual(gradle.read_text().count(module.SIGNING_MARKER),1)
            self.assertNotIn('getByName("debug")',gradle.read_text())
            self.assertIn('throw GradleException',gradle.read_text())
            self.assertIn('maxOf(flutter.minSdkVersion, 24)',gradle.read_text())
            self.assertIn('package com.ehkam.portal',(root/'android/app/src/main/kotlin/com/ehkam/portal/MainActivity.kt').read_text())
            self.assertEqual(pbx.read_text().count('CODE_SIGN_ENTITLEMENTS'),1)
            with info.open('rb') as f:self.assertIn('NSLocationWhenInUseUsageDescription',plistlib.load(f))

    def test_cocoapods_configuration_preserves_content_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'ios/Flutter').mkdir(parents=True)
            pod=root/'ios/Podfile';pod.write_text("# platform :ios, '13.0'\n# custom existing settings\n")
            for mode in ('Debug','Release'):(root/'ios/Flutter'/f'{mode}.xcconfig').write_text('#include "Generated.xcconfig"\n')
            module.configure_cocoapods(root);module.configure_cocoapods(root)
            self.assertIn("platform :ios, '15.0'",pod.read_text())
            self.assertIn('# custom existing settings',pod.read_text())
            self.assertEqual((root/'ios/Flutter/Debug.xcconfig').read_text().count('Pods-Runner.debug.xcconfig'),1)

    def test_android_plugin_meets_locked_dependency_minimum_without_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'android').mkdir();path=root/'android/settings.gradle.kts'
            for existing,expected in [('8.11.1','8.12.1'),('8.12.1','8.12.1'),('8.13.0','8.13.0')]:
                path.write_text('plugins { id("com.google.gms.google-services") version "4.4.4" apply false; id("com.android.application") version "'+existing+'" apply false }')
                module.configure_android_toolchain(root)
                self.assertIn('id("com.android.application") version "'+expected+'"',path.read_text())
                self.assertIn('version "4.4.4"',path.read_text())
            path.write_text('// Custom plugin aliases')
            with self.assertRaises(ValueError):module.configure_android_toolchain(root)
            self.assertEqual(path.read_text(),'// Custom plugin aliases')

if __name__=='__main__':unittest.main()
