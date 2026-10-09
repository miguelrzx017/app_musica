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
android.gradle_dependencies = androidx.media3:media3-exoplayer:1.11.1,androidx.media3:media3-session:1.11.1
orientation = portrait
fullscreen = 0
icon.filename = arquivos/icones/icon.png

android.api = 36
android.minapi = 24
android.ndk = 29
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
p4a.branch = develop
android.permissions = android.permission.READ_MEDIA_AUDIO, android.permission.READ_MEDIA_IMAGES, (name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32), (name=android.permission.WRITE_EXTERNAL_STORAGE;maxSdkVersion=28), android.permission.WAKE_LOCK, android.permission.FOREGROUND_SERVICE, android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK, android.permission.POST_NOTIFICATIONS
android.add_src = src/android
p4a.hook = p4a/hook.py

[buildozer]
log_level = 2
warn_on_root = 1
