# EHKAM mobile — one Flutter project

This folder is the complete Flutter **source project** for both Android and iOS.
Open this folder (the one containing `pubspec.yaml`), not `android/` or `ios/` separately.

- [التعليمات بالعربية](README.ar.md)
- [حالة الفحص والتسليم](PROJECT-DELIVERY.ar.md)
- [Firebase and push setup](PUSH-SETUP.ar.md)

```sh
flutter pub get
python3 tool/firebase_config.py --check --require-hosts
flutter analyze
flutter test
flutter run --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

Native hosts were generated with Flutter 3.41.0 / Dart 3.11.0 and are included,
along with `pubspec.lock`, the Gradle wrapper, the Xcode project, and the iOS Podfile.
Use a Mac with Xcode for iOS. Signing credentials, service-account keys, caches,
Pods, and real employee data are intentionally excluded from source control.
The backend is the Python system in the parent GitHub repository; it is not an
embedded database in this mobile application.

This is source code, not a signed store release. See the delivery report for the
actual checks performed and remaining signing/device/notification checks.
