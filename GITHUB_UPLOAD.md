# 提交工程到 GitHub

主工程目录：`002-701/`（本文件夹）。按下列顺序在 **PowerShell** 或 **Git Bash** 中执行。

## 0. 前置条件

1. 安装 Git：https://git-scm.com/download/win  
2. 注册 GitHub 并登录  
3. 验证：

```powershell
git --version
```

## 1. 进入工程根目录

```powershell
cd "E:\New folder1\002-701\002-701"
```

若使用 zip，先解压 `002-701 (4).zip`，再进入解压后的 `002-701` 文件夹。

## 2. 处理嵌套 Git 仓库（重要）

`Desktop/ros_ws` 内已有独立 `.git`，不处理会导致只提交空引用。删除内层仓库元数据：

```powershell
Remove-Item -Recurse -Force "Desktop\ros_ws\.git"
```

> 仅删除元数据，不影响 `ros_ws` 源码。

## 3. 检查将要提交的内容

```powershell
git init
git add .
git status
```

确认 **未出现** 下列路径（已在 `.gitignore` 中排除）：

- `Desktop/rk3588-offline-bundle/`
- `Desktop/UI/UI/.venv/`
- `*.zip`
- `Desktop/UI/UI/data/app.db`
- `自行记录cursor你别管.md`

若误加入大文件：

```powershell
git rm -r --cached "路径"
```

## 4. 首次提交

```powershell
git commit -m "Initial commit: NovaJoy robot project"
```

## 5. 在 GitHub 创建空仓库

1. 打开 https://github.com/new  
2. Repository name：例如 `NovaJoy-Robot`  
3. 建议选 **Private**  
4. **不要**勾选 “Add a README”  
5. 创建仓库

## 6. 关联远程并推送

将 `你的用户名`、`仓库名` 替换为实际值：

```powershell
git branch -M main
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

HTTPS 推送需 GitHub 账号 + **Personal Access Token**（Settings → Developer settings → Tokens）。

## 7. 克隆后需要自行准备的资源

本仓库**不包含**以下大文件，克隆后需在车端/本机单独部署：

| 资源 | 说明 |
|------|------|
| `rk3588-offline-bundle/` | Sherpa STT/TTS、唤醒词模型 |
| `yolo11/*.pt` / `*.rknn` | 行人检测模型 |
| `Desktop/UI/UI/.venv` | `pip install -r requirements.txt` 重建 |
| `onboard_api.env` | 从 `Desktop/ros_ws/scripts/onboard_api.env.example` 复制并改 IP |

```bash
cp Desktop/ros_ws/scripts/onboard_api.env.example Desktop/ros_ws/scripts/onboard_api.env
```

## 8. 常见问题

| 现象 | 处理 |
|------|------|
| `git` 不是内部命令 | 安装 Git 后重启终端 |
| `error: File ... is 100.00 MB` | 将该路径加入 `.gitignore`，`git rm --cached` 后重新提交 |
| `ros_ws` 在 GitHub 上显示为链接而非文件夹 | 未执行第 2 步，删除 `Desktop/ros_ws/.git` 后重新 `git add` |
| 推送被拒绝 | 检查仓库权限与 Token 是否含 `repo` 权限 |
