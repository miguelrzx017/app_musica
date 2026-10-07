[app]
title = My Music Gospel
package.name = mymusicgospel
package.domain = org.miguelribeiro
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,ttf,svg,db,xml
source.exclude_dirs = .git,.venv,kivy_venv,__pycache__,bin,.buildozer
source.exclude_patterns = arquivos/musicas/*,trilha.log,erro.log
version = 1.0.0
requirements = python3,kivy,pyjnius
orientation = portrait
fullscreen = 0
icon.filename = arquivos/icones/icon.png

android.api = 35
android.minapi = 23
android.ndk = 28c
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.permissions = android.permission.READ_MEDIA_AUDIO, android.permission.READ_MEDIA_IMAGES, (name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32), (name=android.permission.WRITE_EXTERNAL_STORAGE;maxSdkVersion=28), android.permission.WAKE_LOCK, android.permission.FOREGROUND_SERVICE, android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK, android.permission.POST_NOTIFICATIONS
android.add_src = src/android
android.extra_manifest_application_arguments = src/android/extra_manifest_application_arguments.xml

[buildozer]
log_level = 2
warn_on_root = 1
