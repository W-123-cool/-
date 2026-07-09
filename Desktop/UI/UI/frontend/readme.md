# NovaJoy Web 前端入口

后端启动后，手机/平板浏览器直接打开（将 IP 换成你 PC 的局域网地址）：

| 页面 | URL | 用途 |
|------|-----|------|
| **取货端（手机 Web）** | `http://<PC_IP>:8000/pickup` | 注册/登录、发起取货、确认取货（无需 APK） |
| **车载导览** | `http://<PC_IP>:8000/onboard?tab=tour` | 导览 + 送货控制台 |
| **保安总控** | `http://<PC_IP>:8000/security` | 巡逻模式 Web UI |

示例：

```
http://10.1.24.8:8000/pickup
http://10.1.24.10:8000/onboard?tab=tour
http://192.168.1.41:8000/pickup
```

> 若手机浏览器能打开 `http://<PC_IP>:8000/api/robot/state` 并看到 JSON，则 `/pickup` 同样可用（同源，无 CORS 问题）。
