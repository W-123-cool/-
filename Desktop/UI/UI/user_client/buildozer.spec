[app]

title = 楼内取货
package.name = pickupuser
package.domain = org.example.pickup

source.dir = .
source.include_exts = py,otf,kv,png,jpg

version = 0.1.0

requirements = python3,kivy,requests,urllib3,charset_normalizer,idna,certifi

orientation = portrait
fullscreen = 0

[android]

permissions = INTERNET,ACCESS_NETWORK_STATE

# 仅打 arm64 可加快首次构建；需要 32 位机型时改为 armeabi-v7a,arm64-v8a
archs = arm64-v8a

[buildozer]

log_level = 2
warn_on_root = 1
