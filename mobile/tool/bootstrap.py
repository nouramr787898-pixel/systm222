#!/usr/bin/env python3
"""Generate official Flutter Android/iOS hosts, then add reviewed app settings.

Run with an installed stable Flutter SDK. Never edits signing secrets.
"""
import argparse
import plistlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ANDROID='http://schemas.android.com/apk/res/android'
TOOLS='http://schemas.android.com/tools'
ET.register_namespace('android',ANDROID)
ET.register_namespace('tools',TOOLS)
NAME='إحكام المسار'
LOCATION='يستخدم إحكام موقعك عند طلب تسجيل الحضور أو الانصراف فقط للتحقق من موقع العمل.'
SIGNING_MARKER='// EHKAM_RELEASE_SIGNING'
SIGNING='''// EHKAM_RELEASE_SIGNING
val ehkamSigning = Properties()
val ehkamKeyFile = rootProject.file("key.properties")
if (ehkamKeyFile.exists()) FileInputStream(ehkamKeyFile).use { ehkamSigning.load(it) }
if (gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) } && !ehkamKeyFile.exists()) {
    throw GradleException("Configure android/key.properties with your upload keystore before a release build.")
}

'''
SIGNING_BLOCK='''    signingConfigs {
        create("release") {
            if (ehkamKeyFile.exists()) {
                keyAlias = ehkamSigning.getProperty("keyAlias")
                keyPassword = ehkamSigning.getProperty("keyPassword")
                storeFile = file(ehkamSigning.getProperty("storeFile"))
                storePassword = ehkamSigning.getProperty("storePassword")
            }
        }
    }

'''


def identifier(value):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*){2,}',value):
        raise ValueError('Use a valid reverse-domain application identifier.')
    return value


# package_info_plus 10.2.2 in pubspec.lock requires AGP >=8.12.1.
MIN_ANDROID_GRADLE_PLUGIN='8.12.1'


def configure_android_toolchain(root):
    path=root/'android/settings.gradle.kts';text=path.read_text()
    pattern=r'(id\("com\.android\.application"\)\s+version\s+")([0-9]+\.[0-9]+\.[0-9]+)(")'
    match=re.search(pattern,text)
    if not match:raise ValueError('Expected an explicit Android Gradle plugin version; review custom settings before changing them.')
    version=lambda value:tuple(int(part) for part in value.split('.'))
    if version(match[2])<version(MIN_ANDROID_GRADLE_PLUGIN):
        text=text[:match.start(2)]+MIN_ANDROID_GRADLE_PLUGIN+text[match.end(2):]
        path.write_text(text)


def configure(root,android_id,ios_id):
    android_id=identifier(android_id);ios_id=identifier(ios_id)
    configure_android_toolchain(root)
    manifest=root/'android/app/src/main/AndroidManifest.xml'
    tree=ET.parse(manifest);document=tree.getroot()
    existing={p.get('{'+ANDROID+'}name') for p in document.findall('uses-permission')}
    for permission in ('INTERNET','ACCESS_COARSE_LOCATION','ACCESS_FINE_LOCATION','POST_NOTIFICATIONS'):
        name='android.permission.'+permission
        if name not in existing:document.insert(0,ET.Element('uses-permission',{'{'+ANDROID+'}name':name}))
    app=document.find('application')
    if app is None:raise ValueError('Generated Android application element is missing.')
    app.set('{'+ANDROID+'}label',NAME);app.set('{'+ANDROID+'}allowBackup','false');app.set('{'+ANDROID+'}usesCleartextTraffic','false')
    ET.indent(tree,space='    ')
    tree.write(manifest,encoding='utf-8',xml_declaration=True)
    debug=root/'android/app/src/debug/AndroidManifest.xml'
    debug.parent.mkdir(parents=True,exist_ok=True)
    debug_tree=ET.parse(debug) if debug.exists() else ET.ElementTree(ET.Element('manifest'))
    debug_app=debug_tree.getroot().find('application')
    if debug_app is None:debug_app=ET.SubElement(debug_tree.getroot(),'application')
    debug_app.set('{'+ANDROID+'}usesCleartextTraffic','true');debug_app.set('{'+TOOLS+'}replace','android:usesCleartextTraffic')
    ET.indent(debug_tree,space='    ')
    debug_tree.write(debug,encoding='utf-8',xml_declaration=True)
    gradle=root/'android/app/build.gradle.kts';text=gradle.read_text()
    old=re.search(r'namespace\s*=\s*"([^"]+)"',text)
    if not old:raise ValueError('Expected the official Flutter Kotlin Gradle template.')
    old_package=old[1]
    text=re.sub(r'(namespace\s*=\s*)"[^"]+"',lambda m:m[1]+'"'+android_id+'"',text)
    text=re.sub(r'(applicationId\s*=\s*)"[^"]+"',lambda m:m[1]+'"'+android_id+'"',text)
    text=text.replace('minSdk = flutter.minSdkVersion','minSdk = maxOf(flutter.minSdkVersion, 24)')
    if SIGNING_MARKER not in text:
        expected='signingConfig = signingConfigs.getByName("debug")'
        if expected not in text:raise ValueError('Custom signing already exists; review it manually rather than replacing it.')
        text='import java.util.Properties\nimport java.io.FileInputStream\n\n'+text.replace('android {',SIGNING+'android {',1).replace('    buildTypes {',SIGNING_BLOCK+'    buildTypes {',1).replace(expected,'signingConfig = signingConfigs.getByName("release")')
    gradle.write_text(text)
    for source in list((root/'android/app/src').rglob('MainActivity.kt')):
        value=source.read_text().replace('package '+old_package,'package '+android_id)
        # Kotlin source root is found explicitly to support arbitrary namespace depth.
        base=next((parent for parent in source.parents if parent.name=='kotlin'),None)
        if base is None:raise ValueError('Kotlin source root is missing.')
        target=base/Path(*android_id.split('.'))/'MainActivity.kt';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
        if target!=source:source.unlink()
    info=root/'ios/Runner/Info.plist'
    with info.open('rb') as f:values=plistlib.load(f)
    values['CFBundleDisplayName']=NAME;values['NSLocationWhenInUseUsageDescription']=LOCATION
    with info.open('wb') as f:plistlib.dump(values,f,sort_keys=False)
    entitlements=root/'ios/Runner/Ehkam.entitlements'
    if not entitlements.exists():
        with entitlements.open('wb') as f:plistlib.dump({'keychain-access-groups':[]},f)
    pbx=root/'ios/Runner.xcodeproj/project.pbxproj';project=pbx.read_text()
    project=re.sub(r'IPHONEOS_DEPLOYMENT_TARGET = [^;]+;', 'IPHONEOS_DEPLOYMENT_TARGET = 15.0;',project)
    project=re.sub(r'PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);',lambda m:'PRODUCT_BUNDLE_IDENTIFIER = '+ios_id+('.RunnerTests' if m[1].strip().endswith('.RunnerTests') else '')+';',project)
    def build_settings(match):
        block=match[0]
        if 'PRODUCT_BUNDLE_IDENTIFIER' in block and '.RunnerTests' not in block and 'CODE_SIGN_ENTITLEMENTS' not in block:
            block=block.replace('buildSettings = {','buildSettings = {\n\t\t\t\tCODE_SIGN_ENTITLEMENTS = Runner/Ehkam.entitlements;',1)
        return block
    project=re.sub(r'buildSettings = \{.*?\n\t\t\t\};',build_settings,project,flags=re.S)
    pbx.write_text(project)


def configure_cocoapods(root,flutter=None):
    """Keep iOS plugin dependencies reproducible without committing Pods or local paths."""
    podfile=root/'ios/Podfile'
    if not podfile.exists():
        if not flutter:raise ValueError('iOS Podfile is missing; provide an installed Flutter SDK to copy its official template.')
        template=Path(flutter).resolve().parents[1]/'packages/flutter_tools/templates/cocoapods/Podfile-ios'
        if not template.is_file():raise ValueError('Official Flutter iOS Podfile template is missing.')
        podfile.write_bytes(template.read_bytes())
    value=podfile.read_text()
    value=re.sub(r"(?m)^#?\s*platform :ios, ['\"][0-9.]+['\"]", "platform :ios, '15.0'",value)
    podfile.write_text(value)
    for mode in ('Debug','Release'):
        path=root/'ios/Flutter'/f'{mode}.xcconfig'
        if not path.exists():raise ValueError('The official iOS Flutter xcconfig is missing.')
        include=f'#include? "Pods/Target Support Files/Pods-Runner/Pods-Runner.{mode.lower()}.xcconfig"'
        content=path.read_text()
        if include not in content:path.write_text(include+'\n'+content)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-id')
    parser.add_argument('--ios-id')
    parser.add_argument('--configure-only',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    config_paths=[root/'android/app/google-services.json',root/'ios/Runner/GoogleService-Info.plist']
    firebase=None
    if any(path.exists() for path in config_paths):
        if not all(path.exists() for path in config_paths):parser.error('Both Firebase client files must be present before generating hosts.')
        import firebase_config
        firebase=firebase_config.load(root)
    android_id=args.android_id or (firebase['android_id'] if firebase else 'com.ehkam.portal')
    ios_id=args.ios_id or (firebase['ios_id'] if firebase else 'com.ehkam.portal')
    identifier(android_id);identifier(ios_id)
    if firebase and (android_id!=firebase['android_id'] or ios_id!=firebase['ios_id']):parser.error('Application IDs must match the supplied Firebase clients. No files were changed.')
    if firebase:firebase_config.plan(root,firebase) # Validate existing hosts before modifying anything.
    missing=[]
    if not (root/'android/app/build.gradle.kts').exists():missing.append('android')
    if not (root/'ios/Runner.xcodeproj/project.pbxproj').exists():missing.append('ios')
    if not args.configure_only:
        flutter=shutil.which('flutter')
        if not flutter:parser.error('Install Flutter stable (3.41+ / Dart 3.11+) and add flutter to PATH first.')
        if missing:
            # Generate only missing hosts inside THIS Flutter app. Do not overwrite existing files.
            snapshots={path:path.read_bytes() for path in config_paths if path.exists()}
            subprocess.run([flutter,'create','--platforms',','.join(missing),'--project-name','ehkam_mobile','--org','com.ehkam','--no-pub','.'],cwd=root,check=True)
            if any(path.read_bytes()!=data for path,data in snapshots.items()):raise RuntimeError('A supplied Firebase file changed during generation; review the host generator output.')
    elif missing:parser.error('Native hosts are missing; --configure-only cannot generate them.')
    configure(root,android_id,ios_id)
    configure_cocoapods(root,shutil.which('flutter'))
    if firebase:firebase_config.configure(root,require_hosts=True)
    if not args.configure_only:subprocess.run(['flutter','pub','get'],cwd=root,check=True)
    print('Android/iOS hosts configured. Review bundle IDs, signing, icons and privacy before release.')


if __name__=='__main__':main()
