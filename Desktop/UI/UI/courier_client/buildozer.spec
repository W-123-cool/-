[app]

title = 楼内送货
package.name = courieruser
package.domain = org.example.courier

source.dir = .
source.include_exts = py,otf,kv,png,jpg

version = 0.1.0

requirements = python3,kivy,requests,urllib3,charset_normalizer,idna,certifi

orientation = portrait
fullscreen = 0

[android]

permissions = INTERNET,ACCESS_NETWORK_STATE

archs = arm64-v8a

[buildozer]

log_level = 2
warn_on_root = 1
