# buildozer.spec - configuração para gerar o APK do My Music

[app]

title = My Music

package.name = mymusic
package.domain = org.test.mymusic

source.dir = .

source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,db

source.exclude_dirs = bin,venv,__pycache__,.git

version = 0.1

requirements = python3,kivy,sqlite3,pyjnius

orientation = portrait

fullscreen = 0

android.permissions = android.permission.READ_MEDIA_AUDIO, android.permission.READ_MEDIA_IMAGES, (name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32)

android.api = 36
android.minapi = 24

android.ndk = 28c

android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True


# Python-for-Android
p4a.branch = develop

[buildozer]

log_level = 2

warn_on_root = 1