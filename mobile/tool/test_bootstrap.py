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
            self.assertIn('package com.ehkam.portal',(root/'android/app/src/main/kotlin/com/ehkam/portal/MainActivity.kt').read_text())
            self.assertEqual(pbx.read_text().count('CODE_SIGN_ENTITLEMENTS'),1)
            with info.open('rb') as f:self.assertIn('NSLocationWhenInUseUsageDescription',plistlib.load(f))

if __name__=='__main__':unittest.main()
