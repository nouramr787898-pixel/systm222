# تدقيق مشروع إحكام المسار — 4 نوفمبر 2026

الفرع: `codex/professional-hr-upgrade`. آخر التزام عند بدء الفحص من GitHub: `fcc06f8ebc9e67799b9a2b0deb4193f744338591`. التقرير الحالي يرافق تصحيح إعداد بناء Android وإعادة تسليم ZIP مطابق للسورس بعد التصحيح.

## 1. هل mobile مشروع Flutter كامل؟

نعم، من حيث ملفات مشروع التطوير ومضيفَي المنصتين. المجلد ليس إعدادات Firebase وحدها. توجد الملفات التالية فعلًا:

- Flutter: `pubspec.yaml` و`pubspec.lock` و`.metadata` و`lib/` و`assets/` و`test/`.
- Android: `settings.gradle.kts` و`build.gradle.kts` و`app/build.gradle.kts` وGradle wrapper وManifest وMainActivity وموارد التطبيق.
- iOS: `Runner.xcodeproj/project.pbxproj` وworkspace وAppDelegate وSceneDelegate وInfo.plist وentitlements وموارد Runner وملفات Flutter وPodfile.

فُحصت قائمة الملفات الأساسية وأرشيف التسليم وبصماته. لم تكن ملفات المنصتين ناقصة في هذا الفرع، لذلك لم يُعَد توليد مشروع آخر أو حذف السورس. مفاتيح التوقيع وPods والكاش والمسارات المحلية ليست ملفات سورس ناقصة؛ تُجهز على جهاز البناء.

**هذا إثبات لاكتمال ملفات المشروع، وليس شهادة بأن بناء Android وiOS قد نجح.**

## 2. ربط Firebase الفعلي في السورس

- مشروع العميلين: `ehkam-almasar`، ومعرف Android وiPhone: `com.ehkam.portal`.
- Android: ملف `app/google-services.json` مطابق للمعرف، وGoogle Services Gradle plugin ‏4.4.4 معرف في settings ومطبق في app. `applicationId` يطابق الملف.
- iOS: Bundle ID في إعدادات Runner يطابق plist، و`GoogleService-Info.plist` مضاف إلى مجموعة Runner ومرحلة Resources الفعلية للتطبيق، وليس هدف الاختبارات فقط.
- Flutter: `Firebase.initializeApp` يستقبل `DefaultFirebaseOptions.currentPlatform`، والخيارات مشتقة من ملفي العميل.
- iOS يحتوي `remote-notification` و`aps-environment=$(APS_ENVIRONMENT)`؛ Debug مضبوط development وRelease/Profile production. يجب أن يطابق ذلك ملف التهيئة والتوقيع الحقيقيين.

نجح الفحص الصارم `python3 tool/firebase_config.py --check --require-hosts` على الملفات الفعلية. هذا يثبت ربط الإعدادات داخل المشروع، ولا يثبت تفعيل APNs أو صلاحية إرسال الخادم أو استقبال Push على هاتف.

## 3. تصحيح اكتُشف أثناء التدقيق

الاعتماد المقفل `package_info_plus 10.2.2` يشترط **AGP 8.12.1 أو أحدث** وGradle 8.13 أو أحدث. كان إعداد المشروع يستخدم AGP 8.11.1.

صُحح AGP إلى 8.12.1 في `android/settings.gradle.kts`، وأضيفت حماية في أداة bootstrap تمنع توليد إعداد أقل من ذلك، مع اختبار يحافظ على الإصدارات الأعلى ولا يغير plugin Google Services أو إعدادات مخصصة لا يفهمها. Gradle 8.14 وKotlin 2.2.20 الموجودان يفيان بالمتطلبات المعلنة. لم تُغير إصدارات حزم Dart المقفلة ولم يُضف مشروع Firebase جديد.

## 4. الفحوص وما تم بناؤه فعليًا

| الإجراء | النتيجة وحدودها |
| --- | --- |
| جلب أحدث مرجع GitHub ومقارنته بالنسخة المفحوصة | تطابق مع fcc06f8 عند بداية التدقيق |
| Flutter pub get | سبق نجاحه وتوليد lock؛ أُعيد على نسخة مستخرجة باستخدام الكاش مع --offline |
| flutter analyze --no-pub | شُغّل فعلًا ونجح دون ملاحظات |
| flutter test --no-pub | شُغّل فعلًا ونجحت 7 اختبارات قواعد وواجهة؛ ليس اختبار هاتف فعليًا |
| أدوات Python للتجهيز وFirebase والتغليف | نجحت 15 حالة بعد تصحيح AGP |
| فحص Firebase الصارم | نجح على مشروع Android وiOS الموجودين |
| فحص ZIP | مجلد Flutter جذري واحد، قائمة مصدر مطابقة للمستودع، CRC وبصمات SHA256، دون ملفات توقيع أو هوية خدمة أو قواعد بيانات |
| بناء Android في النسخة السابقة fcc06f8 | تمت محاولة فعلية ولم تنجح؛ توقفت عند تنزيل flutter_embedding_debug بسبب HTTP 403 |
| بناء Android بعد تصحيح AGP في هذا التدقيق | لا يوجد APK/AAB ناجح؛ الحظر الحالي على storage.googleapis.com ما زال قائمًا، ولا يُدّعى اجتياز البناء الأصلي |
| بناء iOS | لم يُنفذ؛ بيئة الفحص Linux وليست Mac/Xcode |
| توقيع المتاجر أو إرسال/استقبال Push فعلي | لم يُنفذ ولم يُثبت نجاحه |

الفحوص تستخدم Flutter 3.41.0 وDart 3.11.0 داخل بيئة Cirrus Labs المعزولة المثبتة ببصمة الصورة المذكورة في PROJECT-DELIVERY.ar.md. مراجعة Flutter تطابق الوسم الرسمي 3.41.0. لم تُستعمل قاعدة بيانات الموقع في التدقيق أو في اختبارات Flutter وأدوات الإعداد.

## 5. النقل إلى بيئة Android

المكونات المطابقة للمشروع الحالي:

- Flutter 3.41.0/Dart 3.11.0 كخط أساس تم فحصه، أو إصدار متوافق بعد إعادة التحليل والاختبارات.
- JDK 17 أو JDK متوافق مع Gradle/AGP المحددين؛ استخدمت بيئة الفحص JDK 21.
- AGP 8.12.1 وGradle wrapper 8.14 وKotlin 2.2.20، موجودة في ملفات المشروع.
- Android SDK Platform 36 وBuild Tools 35.0.0 وPlatform Tools وNDK `28.2.13676358`. بعض المكتبات تستدعي أيضًا Platforms 34 و35؛ وفّرها أو اسمح بتثبيتها من مستودع Google. الحد الأدنى للجهاز API 24.
- اتصالات HTTPS موثوقة لمستودعات Flutter وDart وGradle وGoogle/Maven. تحديدًا `storage.googleapis.com` لازم لتنزيل حزم المحرك الأصلية، إضافةً إلى `pub.dev` و`dl.google.com` و`repo.maven.apache.org` و`plugins.gradle.org` و`services.gradle.org` ونطاقات إعادة توجيه التنزيل المعتمدة. لا تعطل TLS.

من جذر مشروع Flutter بعد فك الحزمة:

```sh
flutter doctor -v
flutter pub get --enforce-lockfile
python3 tool/firebase_config.py --check --require-hosts
flutter analyze
flutter test
flutter build apk --debug --no-pub --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

استبدل عنوان الخادم بالقيمة المعتمدة. بعد نجاح APK والتجربة، جهّز مفتاح upload keystore وملف `android/key.properties` محليًا خارج GitHub، ثم:

```sh
flutter build appbundle --release --no-pub --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

لا تستخدم مفتاح debug للنشر. إعداد Gradle يرفض release دون إعداد التوقيع. لا يكفي نجاح تنزيل الاعتماديات لإعلان نجاح البناء.

## 6. النقل إلى Mac/Xcode

- Mac يستطيع تشغيل نسخة Xcode متوافقة مع Flutter وFirebase Apple SDK المحدد، وFlutter/Dart وأدوات Xcode Command Line Tools وCocoaPods.
- مسار CocoaPods الحالي يثبت Firebase Apple SDK **12.19.3** وفق ملف firebase_core 5.1.0، وليس افتراض أحدث إصدار 13 من Swift Package Manager. مصدر SDK 12.19.3 يعلن Swift tools 6.1 وiOS 15 في Package.swift؛ استخدم Xcode 16.3 أو أحدث متوافقًا مع macOS، وراجع أيضًا متطلبات إصدار Xcode الحالية لدى Apple قبل النشر.
- الحد الأدنى للتطبيق iOS 15.0 في المشروع وPodfile. اسمح بتنزيل Pods وملفات محرك Flutter الخاصة بـiOS، ولا تنقل مسارات Generated.xcconfig من جهاز آخر.

```sh
flutter doctor -v
flutter pub get --enforce-lockfile
cd ios
pod install
cd ..
flutter build ios --debug --no-codesign --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

هذا أمر للتحقق من البناء دون توقيع، وليس IPA صالحًا للتثبيت والنشر. لإجراء اختبار APNs على iPhone، جهز Apple Team وSigning وProvisioning Profile يدعم Push ومعرف `com.ehkam.portal`، ثم ابنِ نسخة موقعة. إصدار المتجر:

```sh
flutter build ipa --release --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

لا يوجد في هذا التسليم دليل نجاح هذه الأوامر على Mac؛ المطلوب تنفيذها وتسليم سجلات النجاح.

## 7. ما يخص APNs فقط

ملف GoogleService-Info.plist لا يحتوي مفتاح APNs ولا يثبت رفعه إلى Firebase. المطلوب من مالك حساب Apple:

1. App ID وTeam والتوقيع وملف تهيئة يدعم Push Notifications.
2. APNs authentication key الصحيح، مع Key ID وTeam ID، وربطه بتطبيق iOS في Firebase Console.
3. مراجعة Push Notifications وBackground Modes → Remote notifications في Xcode، وتطابق APS_ENVIRONMENT مع ملف التهيئة والتصدير.

لا تُدخل ملف p8 أو بيانات Apple السرية في GitHub أو ZIP. لا تنشئ مشروع Firebase آخر بدل المشروع الحالي.

## 8. ما يخص Firebase على Railway فقط

هذه إعدادات خادم مستقلة عن ملفات Android وiOS:

- تثبيت requirements.txt الخاص بخادم Python.
- تفعيل Firebase Cloud Messaging HTTP v1 API للمشروع `ehkam-almasar`.
- توفير هوية خدمة مخولة بإرسال FCM للمشروع الصحيح، مثل دور Firebase Cloud Messaging API Admin أو صلاحيات إرسال مكافئة محدودة، أو Workload Identity مدعومة.
- إعداد `FIREBASE_PROJECT_ID=ehkam-almasar` على خدمة Railway.
- عند استخدام ملف هوية خدمة، ضعه في ملف محمي تجهزه آلية نشر آمنة خارج المستودع، واضبط `GOOGLE_APPLICATION_CREDENTIALS` على مساره الموجود فعليًا. كتابة المسار وحدها دون توفير الملف لا تكفي. لا تُستخدم ملفات إعداد العميل كهوية خدمة.
- السماح باتصال الخادم بـFCM وخدمة إصدار رموز الهوية المختارة، ومراجعة طابور الإشعارات وحالات الإرسال دون تسجيل الرموز أو المفاتيح.

لم تُراجع صلاحيات Firebase Console أو إعدادات Railway الخارجية لعدم توفر وصول مخول، ولم تُغيَّر متغيرات الإنتاج أو قاعدة بيانات الموقع.

## 9. ما يحتاج أجهزة فعلية

بعد استكمال الخادم وAPNs وبناء التطبيقين، اختبر على Android وiPhone: السماح/الرفض للإشعارات، إنشاء الطلب وتغيير حالته والتعليقات والبصمة، وصول Push في المقدمة والخلفية والشاشة مقفولة، النقر وقراءة التفاصيل، إيقاف الإشعارات وتسجيل الخروج وتبديل الحساب، مع فحص الصلاحيات ونطاق الشركة والمرفقات والاستعادة. الإيقاف القسري للتطبيق وإطفاء الهاتف أو غياب الإنترنت له قيود نظامية، ولا يُعلن تسليم فوري في هذه الحالات.

## 10. حماية البيانات ودقة الوصف

لم تُفتح أو تُعدل قاعدة بيانات الموقع الحقيقي. لم تُضف هوية Admin SDK أو مفاتيح APNs أو مفاتيح توقيع إلى GitHub أو ملف التسليم. ملفا Firebase الموجودان إعدادات عميل ومعرفات تطبيق، وليسا مفاتيح إدارة للخادم. فحص ZIP بحث عن ملفات المفاتيح والتوقيع وقواعد البيانات ومفاتيح PEM وJSON من نوع service_account ولم يجدها.

**التسليم: مشروع Flutter موحد كامل الملفات، مع تصحيح متطلب بناء مثبت وتقرير فحوص. لا يوجد إعلان بأن نسخ Android/iOS جاهزة للنشر أو بأن إشعارات الهاتف تعمل فعليًا قبل دليل البناء والتوقيع واختبار الأجهزة.**
