[app]

title = NovaJoy 取货
package.name = novajoypickup
package.domain = com.novajoy

# 从 UI 仓库根目录打包（含 user_client、novajoy_ui）；根目录 main.py 为 p4a 入口
source.dir = ..
source.main = main.py
source.include_exts = py,png,jpg,kv,otf
source.exclude_patterns = backend/*,courier_client/*,onboard_client/*,ros_ws/*,frontend/*,scripts/*,car_ui/*,.buildozer/*,.venv/*

version = 0.1.0

requirements = python3,kivy,requests,urllib3,charset_normalizer,idna,certifi

orientation = portrait
fullscreen = 0

# 启动图标暂不更换（沿用 buildozer 默认）；品牌 PNG 已含在 assets/branding

[android]

permissions = INTERNET,ACCESS_NETWORK_STATE

# arm64 覆盖绝大多数现网 Android；需 32 位机型时改为 armeabi-v7a,arm64-v8a
archs = arm64-v8a

# 允许 HTTP 明文（内网联调 http://192.168.x.x:8000）；上架生产建议改 HTTPS 并删除
android.manifest.application_attributes = android:usesCleartextTraffic=true

[buildozer]

log_level = 2
warn_on_root = 1
