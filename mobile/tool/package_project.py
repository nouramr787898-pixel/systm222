#!/usr/bin/env python3
"""Package ONE complete Flutter source folder; exclude caches, keys and user data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

REQUIRED=(
    'pubspec.yaml','pubspec.lock','.metadata','lib/main.dart','lib/firebase_options.dart','assets/logo.png',
    'android/app/build.gradle.kts','android/build.gradle.kts','android/settings.gradle.kts',
    'android/gradle.properties','android/gradlew','android/gradlew.bat',
    'android/gradle/wrapper/gradle-wrapper.properties','android/gradle/wrapper/gradle-wrapper.jar',
    'android/app/src/main/AndroidManifest.xml','android/app/src/main/kotlin/com/ehkam/portal/MainActivity.kt',
    'android/app/google-services.json','ios/Runner/GoogleService-Info.plist',
    'ios/Runner.xcodeproj/project.pbxproj','ios/Runner.xcworkspace/contents.xcworkspacedata',
    'ios/Podfile','ios/Runner/AppDelegate.swift','ios/Runner/Info.plist','ios/Runner/Ehkam.entitlements',
    'ios/Flutter/Debug.xcconfig','ios/Flutter/Release.xcconfig','ios/Flutter/AppFrameworkInfo.plist',
)
EXCLUDED_DIRS={'.git','.dart_tool','build','.gradle','Pods','.symlinks','ephemeral','xcuserdata','DerivedData','__pycache__','.idea','.cxx','.kotlin','captures'}
EXCLUDED_FILES={'local.properties','key.properties','Generated.xcconfig','flutter_export_environment.sh','GeneratedPluginRegistrant.java','GeneratedPluginRegistrant.h','GeneratedPluginRegistrant.m','.flutter-plugins-dependencies','.flutter-plugins','.DS_Store'}
SECRET_EXTENSIONS={'.jks','.keystore','.p8','.p12','.mobileprovision','.pem','.key','.sqlite3','.db','.pyc'}


def include(path):
    return not (set(path.parts)&EXCLUDED_DIRS or path.name in EXCLUDED_FILES or path.suffix.lower() in SECRET_EXTENSIONS or path.suffix.lower()=='.iml' or path.name=='.env' or path.name.startswith('.env.') or 'service-account' in path.name.lower())


def package(root,output):
    missing=[name for name in REQUIRED if not (root/name).is_file()]
    if missing:raise ValueError('Incomplete Flutter source; missing: '+', '.join(missing))
    output=output.resolve()
    if output.is_relative_to(root.resolve()):raise ValueError('Write the ZIP outside the Flutter folder to avoid recursive archives.')
    files={}
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if path.is_symlink():continue
        if path.is_file() and include(relative):files[relative.as_posix()]=path.read_bytes()
    manifest={'project':'ehkam_mobile','format':'one Flutter source folder with Android and iOS','sha256':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
    files['SOURCE-MANIFEST.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in files.items():
            info=zipfile.ZipInfo('ehkam_mobile/'+name);info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100755 if name=='android/gradlew' else 0o100644)<<16
            archive.writestr(info,raw)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:raise ValueError('Archive CRC verification failed.')
        for name,digest in manifest['sha256'].items():
            if hashlib.sha256(archive.read('ehkam_mobile/'+name)).hexdigest()!=digest:raise ValueError('Archive manifest verification failed.')
    output.with_suffix('.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
    return len(files)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    try:count=package(root,args.output)
    except ValueError as error:parser.error(str(error))
    print(f'One Flutter project packaged: {count} files. CRC and SHA256 manifest verified.')


if __name__=='__main__':main()
