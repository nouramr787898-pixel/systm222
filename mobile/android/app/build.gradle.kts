import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.google.gms.google-services")
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// EHKAM_RELEASE_SIGNING
val ehkamSigning = Properties()
val ehkamKeyFile = rootProject.file("key.properties")
if (ehkamKeyFile.exists()) FileInputStream(ehkamKeyFile).use { ehkamSigning.load(it) }
if (gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) } && !ehkamKeyFile.exists()) {
    throw GradleException("Configure android/key.properties with your upload keystore before a release build.")
}

android {
    namespace = "com.ehkam.portal"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.ehkam.portal"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = maxOf(flutter.minSdkVersion, 24)
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (ehkamKeyFile.exists()) {
                keyAlias = ehkamSigning.getProperty("keyAlias")
                keyPassword = ehkamSigning.getProperty("keyPassword")
                storeFile = file(ehkamSigning.getProperty("storeFile"))
                storePassword = ehkamSigning.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("release")
        }
    }
}

flutter {
    source = "../.."
}
