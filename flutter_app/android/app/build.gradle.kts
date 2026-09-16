plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "dev.kiteclipper.kite_clipper_flutter"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "dev.kiteclipper.kite_clipper_flutter"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName

        // 実機（arm64）向けのライブラリだけを含める指定。ただしFlutterのdebugビルドでは
        // 後から上書きされて効かないため、実際の除外は下の packaging で行う。
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    // 実機（arm64）以外のネイティブライブラリをAPKに含めない。
    // 再生エンジンやONNX Runtimeはプラグイン由来のため、パッケージ段階で除外する。
    // x86_64のエミュレータでは動かなくなる。
    packaging {
        jniLibs {
            excludes += listOf("lib/x86_64/**", "lib/x86/**", "lib/armeabi-v7a/**")
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    // AI候補の測定用。CLIP画像エンコーダー(ONNX)をアプリ内で実行する。
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
