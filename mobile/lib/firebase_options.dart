// Derived from the supplied Firebase client configuration by tool/firebase_config.py.
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) return android;
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.iOS) return ios;
    throw UnsupportedError('Firebase is configured for Android and iOS only.');
  }

  static const android = FirebaseOptions(
    apiKey: "AIzaSyAxyYy-Z6FHatJGqtS3ggHYdOFMCOedRjw",
    appId: "1:581075257560:android:29b0176ed8e26bdcf56fad",
    messagingSenderId: "581075257560",
    projectId: "ehkam-almasar",
    storageBucket: "ehkam-almasar.firebasestorage.app",
  );

  static const ios = FirebaseOptions(
    apiKey: "AIzaSyCQNTjGT3TQbbFtRXjvkq7io-n2WheJge4",
    appId: "1:581075257560:ios:3239951d8dd7b7c5f56fad",
    messagingSenderId: "581075257560",
    projectId: "ehkam-almasar",
    iosBundleId: "com.ehkam.portal",
    storageBucket: "ehkam-almasar.firebasestorage.app",
  );
}
