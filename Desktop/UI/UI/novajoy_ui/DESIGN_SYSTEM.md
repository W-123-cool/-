# NovaJoy Design System

控制台风格 Kivy UI 规范（仅视觉层）。

## 布局骨架

```
ControlTopBar（80dp）
StatusStrip（72dp，4 指标）
Main Workspace（横向：导航 | 工作区 | 状态栏）
MessageStream（96–108dp 底部消息流）
```

## 组件库（`novajoy_ui/`）

| 组件 | 文件 | 说明 |
|------|------|------|
| `ControlTopBar` | `components.py` | Logo + 页面名 + 状态芯片 |
| `StatusStrip` / `NovaJoyStatusCard` | `components.py` | 状态监控条 |
| `NovaJoyCard` | `components.py` | 12dp 圆角功能卡片 |
| `MessageStream` | `components.py` | 底部消息流（替代 TextInput 日志） |
| `nvj_button` | `widgets.py` | primary / secondary / danger |
| `C` | `theme.py` | Color Token |

## Color Token

主背景 `#081A2F` · 卡片 `#122D4A` · 主按钮 `#00D4FF` · 次按钮 `#203A5A` · 危险 `#FF5252`

## 客户端映射

| 客户端 | 布局 |
|--------|------|
| `user_client` | 顶栏 + 状态条 + 左(连接/账户)右(取货/到站) + 消息流 |
| `courier_client` | 顶栏 + 状态条 + 左(连接/维护)右(投件/送达/队列) + 消息流 |
| `onboard_client` | 顶栏 + 左导航 + 中 Screen + 右状态卡 + 底部集成事件 |

## Interaction

按钮 120–180ms ease-out；禁止长表单无限 ScrollView 堆叠。

## Kivy 注意

**禁止** 在 `Widget` / `Layout` 子类上使用 `self.top`、`self.left`、`self.right`、`self.bottom` 作为自定义属性名——它们与 Kivy 坐标属性冲突，会导致 `padding` 设置时报 `TypeError: ControlTopBar - int`。顶栏请命名为 `self.top_bar`。
