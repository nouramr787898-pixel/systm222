# مشروع Flutter الموحد لتطبيق إحكام المسار

هذا **مجلد مشروع واحد** للمنصتين Android وiPhone. افتح المجلد الذي يحتوي `pubspec.yaml`؛ لا تحتاج سورس Android منفصلًا وسورس iPhone منفصلًا. اللغة Dart وإطار العمل Flutter. إصدار التطبيق الحالي `0.1.1+2`، ومعرّف المنصتين `com.ehkam.portal`، ومشروع Firebase الحالي `ehkam-almasar`.

## المحتويات

- `lib/`: شاشات التطبيق وواجهات API والجلسات والإشعارات واستعادة كلمة المرور والمساعد.
- `android/`: إعدادات Gradle وGradle wrapper وMainActivity وManifest وموارد Android وملف Firebase.
- `ios/`: مشروع Xcode وworkspace وRunner وملفات Flutter وPodfile وموارد iOS وملف Firebase وentitlements.
- `pubspec.yaml` و`pubspec.lock`: الاعتماديات وإصداراتها المحلولة.
- `.metadata`: بيانات مشروع Flutter الرسمية.
- `assets/`: شعار التطبيق.
- `test/`: اختبارات Dart والواجهة.
- `tool/`: فحص إعداد Firebase وتجهيز إعدادات المنصتين وتغليف المشروع في ZIP واحد واختبارات هذه الأدوات.

تم توليد مضيفَي Android وiOS باستخدام Flutter الرسمي 3.41.0، بمراجعة `44a626f4f0027bc38a46dc68aed5964b05a83c18` المطابقة لوسم Flutter الرسمي، داخل صورة Cirrus Labs المعزولة والمثبتة بالبصمة. لم يُستخدم مشروع تطبيق آخر بدل السورس الحالي.

راجع [تقرير التسليم الحالي](PROJECT-DELIVERY.ar.md) لمعرفة نتيجة التحليل والاختبارات والبناء بدقة. اكتمال ملفات السورس لا يعني توقيع نسخة متجر أو تجربة كل الوظائف على أجهزة حقيقية.

## فتح المشروع وتجربته

المتطلبات: Flutter stable ‏3.41+/Dart ‏3.11+، وAndroid SDK وJava المتوافقان للبناء على Android. لبناء iPhone يلزم Mac وXcode وCocoaPods عند استخدام مسار الاعتماديات الحالي. الحد الأدنى المضبوط: Android API 24 وiOS 15.

داخل هذا المجلد:

```sh
flutter pub get
python3 tool/firebase_config.py --check --require-hosts
flutter analyze
flutter test
flutter run --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

استبدل عنوان المثال بعنوان الخادم الصحيح. العنوان الافتراضي في الكود `https://systemehkam.online`. لا يتضمن التطبيق قاعدة بيانات موظفين محلية، ولا ينشئ الحسابات التجريبية على الموقع الحقيقي. الخادم Python موجود في جذر مستودع النظام على فرع `codex/professional-hr-upgrade`، خارج مجلد تطبيق Flutter. يجب نشر تحديثاته المطلوبة للجلسات والإشعارات قبل اختبار الهاتف معه.

احتفظ بـ`pubspec.lock`. ملفات المسارات المحلية مثل `android/local.properties` و`ios/Flutter/Generated.xcconfig` وملفات تسجيل المكتبات تُنشأ بأدوات Flutter على جهاز المطور؛ لا تُنسخ مسارات جهاز البناء السابق ولا تحتاج لإدخالها في GitHub.

## Firebase والإشعارات

ملفا العميل الأصليان موجودان في:

- `android/app/google-services.json`
- `ios/Runner/GoogleService-Info.plist`

وتوجد خيارات المنصتين في `lib/firebase_options.dart`. إعداد Google Services Gradle plugin مطبق على Android، وملف plist مضمّن في موارد Runner. إعداد iOS يحتوي remote-notification وaps-environment عبر `APS_ENVIRONMENT`، ويحتاج مراجعة تطابقه مع التوقيع الفعلي.

لا تنشئ مشروع Firebase جديدًا ولا تغيّر معرفات التطبيق دون ملفات عميل مطابقة. اتبع [تعليمات الإشعارات](PUSH-SETUP.ar.md) لإعداد APNs وهوية إرسال الخادم وتجربة الهاتف. لا يتضمن ملفا العميل مفتاح APNs أو هوية الخدمة. لا يوجد ادعاء باختبار استقبال إشعار حقيقي.

يحافظ كود الهاتف على واجهة registration token المتوافقة مع `message.token` في الخادم؛ استدعاءاتها المدعومة في Firebase Messaging 17.0.1 معلّمة deprecated لصالح نموذج التسجيل الجديد. وُثّقت الاستثناءات عند الاستدعاءات فقط، ولا يجوز تبديلها بمعرف Firebase Installation عشوائيًا دون مراجعة عقد إرسال الخادم واختباره.

## الاختبارات والحسابات التجريبية

الاختبارات داخل `test/` تستخدم بيانات ومزود HTTP وهميين، وتختبر القواعد الأساسية والواجهة العربية والاستعادة، ولا تتصل بأشخاص أو تختبر تسليم Push حقيقي.

الحسابات التجريبية التي طلبها صاحب المشروع في حزمة خادم مراجعة منفصلة، وليست في بيانات موقع الإنتاج أو داخل assets التطبيق. على المطور نشر خادم المراجعة في بيئة HTTPS مستقلة واختيار آلية آمنة لوصول مراجعي المتاجر إلى النسخة النهائية؛ نسخة مبنية بعنوان التجربة تتصل به لجميع مستخدميها. راجع الحسابات وربط الخادم قبل إرسال التطبيق للمراجعة.

كلمة المرور من 8 إلى 128 حرفًا دون اشتراط أرقام أو رموز. رمز التحقق المؤقت منفصل عن كلمة المرور. تبقى صلاحيات تغيير كلمات المرور والاستعادة وفق سياسة الخادم.

## البناء والتوقيع

Android Debug للتجربة:

```sh
flutter build apk --debug --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

للنشر: يجهز صاحب الحساب upload keystore، ويستخدم `tool/key.properties.example` لإعداد `android/key.properties` محليًا. إعداد Gradle يرفض بناء release بلا مفتاح رفع؛ لا يستخدم توقيع debug للنشر.

```sh
flutter build appbundle --release --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

على Mac بعد ضبط Apple Team وملف التهيئة والتوقيع وإعداد APNs:

```sh
flutter build ipa --release --dart-define=EHKAM_API_URL=https://YOUR-SYSTEM-HOST
```

لا تُرفع مفاتيح Android أو Apple أو هويات الخدمة إلى GitHub. راجع الأيقونات النهائية ولقطات المتاجر ووصف التطبيق والخصوصية والدعم وإفصاحات البيانات قبل الإرسال. الأيقونات الأصلية التي ولّدها Flutter تحتاج اعتماد الشكل النهائي للمتاجر من صاحب التطبيق.

## إعادة تجهيز إعدادات المنصتين والتغليف

ملفات المنصتين موجودة بالفعل. لا يلزم تشغيل `flutter create` لفتح المشروع أو تجربته.

إذا استبدلت ملفي عميل Firebase بنسختين معتمدتين:

```sh
python3 tool/firebase_config.py
python3 tool/firebase_config.py --check --require-hosts
```

لإعادة تطبيق إعدادات التجهيز مع الحفاظ على الملفات المخصصة قدر الإمكان، راجع أداة `tool/bootstrap.py --configure-only` أولًا. الأداة لا تولد مضيفين موجودين من جديد، وترفض بعض التعارضات بدل استبدالها بصمت.

لتسليم المشروع في **ملف مضغوط واحد**، من مجلد Flutter:

```sh
python3 tool/package_project.py --output ../ehkam-flutter-project-0.1.1.zip
```

الأداة ترفض التغليف إذا نقص أي ملف أساسي من المشروع أو المنصتين، وتستبعد الكاش وPods والمسارات المحلية والمفاتيح وقواعد البيانات، وتضيف بصمات SHA256 لكل ملف وتفحص سلامة ZIP. ملف التسليم يحتوي مجلدًا جذريًا واحدًا اسمه `ehkam_mobile`.
