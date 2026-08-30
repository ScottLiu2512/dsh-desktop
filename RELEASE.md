# 发布流程（Release Guide）

本文档记录 DSH Desktop 从「改版本号」到「发布到 GitHub Releases」的完整流程，
基于 v1.0.2 的实际发布操作整理。

## 前置条件

| 工具 | 说明 |
|---|---|
| Python 3.10+ | 与运行时一致 |
| PyInstaller | pip install -r requirements-dev.txt（含 pyinstaller） |
| Inno Setup 6 | 默认安装路径 C:\Program Files (x86)\Inno Setup 6\ISCC.exe |
| Git + GitHub 凭据 | 已配置凭据管理器（git config --get credential.helper 应为 manager） |
| 发布用 token | 凭据管理器中的 GitHub token 需有 repo 权限（用于创建 Release / 上传资产） |

> 版本号唯一权威来源：dsh_gui/__init__.py 的 __version__。
> dsh_gui.spec 会正则提取它；generate_promo_images.py 也动态读取。无需手动改。

---

## 一、bump 版本号（3 处）

```text
# 1. 权威版本号
#    dsh_gui/__init__.py  ->  __version__ = "X.Y.Z"

# 2. 安装脚本（必须手动同步！）
#    dsh_client_setup.iss ->  #define MyAppVersion "X.Y.Z"

# 3. README 版本说明（必须手动同步！）
#    README.md            ->  > 当前版本：vX.Y.Z ｜ [Releases](...)
```

> 注意：dsh_client_setup.iss 和 README.md 不会自动跟随 __init__.py，
> 漏改会导致「exe 内版本」与「安装包版本 / 文档版本」不一致。

## 二、提交并推送

```bash
git add dsh_gui/__init__.py dsh_client_setup.iss README.md
git commit -m "build: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin main vX.Y.Z     # 一次推送 main + tag
```

### 注意：GitHub 网络不通时（中国大陆网络常见）

症状：git push 报 Failed to connect to github.com:443 / Connection was reset，
但 api.github.com、ssh.github.com 正常。原因是 github.com 的 DNS 被污染，
解析到的 IP（如 20.205.243.166）间歇性不可达。

解决办法：临时把 github.com 指向一个当前可达的 IP，推送后立即恢复 hosts。

```powershell
# 1. 先探测当前可达的 GitHub IP（返回 True 的可用）
$ips = @('140.82.112.3','140.82.113.3','140.82.114.3','140.82.116.3','140.82.121.3','20.205.243.166')
foreach ($ip in $ips) {
  $t = Test-NetConnection -ComputerName $ip -Port 443 -WarningAction SilentlyContinue -InformationLevel Quiet
  "$ip : $t"
}

# 2. 写入临时 hosts（用 .NET 绕开安全软件的文件锁），推送，恢复
$hosts = 'C:\Windows\System32\drivers\etc\hosts'
$orig  = [System.IO.File]::ReadAllText($hosts)
$entry = '# dsh temp' + [Environment]::NewLine + '140.82.112.3 github.com' + [Environment]::NewLine
[System.IO.File]::WriteAllText($hosts, $orig + $entry)
git push origin main vX.Y.Z
[System.IO.File]::WriteAllText($hosts, $orig)   # 必须恢复！
```

网络是间歇性的：某 IP 可能几分钟内从可达变不可达。建议写个小循环轮换 IP
重试（每轮 30 秒），或直接跑若干分钟的后台脚本。

## 三、打包

```bash
# 1. 生成单文件 exe（约 3-4 分钟，产物 dist/DSH-Desktop.exe）
python -m PyInstaller dsh_gui.spec --noconfirm --clean

# 2. 生成安装包（约 12 秒，产物 installer_output/DSH-Desktop-Setup.exe）
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' dsh_client_setup.iss
```

打包前确认：release/screenshot.png 是最新截图（该目录被 gitignore，不会自动更新）。

## 四、创建 Release 并上传资产

用 GitHub API（凭据管理器里的 token）：

```powershell
# 取 token（不打印到日志）
$credInput = "protocol=https" + [char]10 + "host=github.com" + [char]10 + [char]10
$token = (($credInput | git credential fill) | Select-String '^password=').Line.Substring(9)
$headers = @{ Authorization = "Bearer $token"; 'User-Agent' = 'dsh-desktop-release'; 'Accept' = 'application/vnd.github+json' }

# 1. 创建 Release（tag 需先推送成功）
$body = @{
  tag_name = 'vX.Y.Z'
  name     = 'DSH Desktop vX.Y.Z'
  body     = '<更新说明，建议包含 新增 / 修复 / 其他 三节>'
  draft    = $false
  prerelease = $false
} | ConvertTo-Json
$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/ScottLiu2512/dsh-desktop/releases' -Method Post -Headers $headers -ContentType 'application/json' -Body $body
# 记下 $rel.id

# 2. 上传资产（uploads.github.com，三个都要）
$rid = $rel.id
curl.exe -sS -X POST -H "Authorization: Bearer $token" -H "Content-Type: image/png" --data-binary '@release/screenshot.png' "https://uploads.github.com/repos/ScottLiu2512/dsh-desktop/releases/$rid/assets?name=screenshot.png"
curl.exe -sS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/octet-stream" --data-binary '@installer_output/DSH-Desktop-Setup.exe' "https://uploads.github.com/repos/ScottLiu2512/dsh-desktop/releases/$rid/assets?name=DSH-Desktop-Setup.exe"
curl.exe -sS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/octet-stream" --data-binary '@dist/DSH-Desktop.exe' "https://uploads.github.com/repos/ScottLiu2512/dsh-desktop/releases/$rid/assets?name=DSH-Desktop.exe"
```

> 215MB 的大文件上传耗时数分钟，建议放后台任务；失败就重试（同名资产重复上传会 422，
> 可先查 GET /releases/tags/vX.Y.Z 的 assets 列表，用 DELETE /releases/assets/{id} 删旧的）。

## 五、验证

```powershell
# 1. Release 与资产齐全（screenshot.png / Setup.exe / exe 三个都 state=uploaded）
$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/ScottLiu2512/dsh-desktop/releases/tags/vX.Y.Z' -Headers $headers
$rel.assets | ForEach-Object { "$($_.name) $($_.size) state=$($_.state)" }

# 2. README 里的 latest 链接应返回 200（重定向到本版本资产）
curl.exe -sS -L -o NUL -w "%{http_code}" "https://github.com/ScottLiu2512/dsh-desktop/releases/latest/download/screenshot.png"
```

## 六、发布后提醒

- 旧版本客户端（v1.0.1+）会自动检测到新版本并提示升级（新版本内置一键升级）。
- 用户在 GitHub 首页看到的新 release 即最新版本；README 截图链接
  （releases/latest/download/screenshot.png）无需改动，自动指向最新。

---

## 常见问题（FAQ）

**Q：git push 一直超时/RST，hosts 也试过？**
A：网络窗口可能极短。用「探测 IP → 写 hosts → push → 恢复」的循环脚本后台跑
10-20 分钟；或换时段再试。SSH（ssh.github.com:443）通常可达，但需要
admin:public_key 权限的 token 注册公钥——发布用 OAuth token 一般没有该 scope。

**Q：上传资产 422？**
A：同名资产已存在。先 GET .../releases/tags/vX.Y.Z 查看 assets，用
DELETE /releases/assets/{id} 删除旧的再传。

**Q：创建 Release 时 tag 不存在？**
A：必须先把 tag 推送成功（git push origin vX.Y.Z），再调 API 创建。

**Q：hosts 写入失败（文件被占用）？**
A：安全软件（如 AlibabaProtect）会锁 hosts。用
[System.IO.File]::WriteAllText 全量写入可绕过；别用 Add-Content。

**Q：PyInstaller 打包后 WebEngine 打不开/卡死？**
A：main.py 已默认设置 QTWEBENGINE_DISABLE_SANDBOX=1；仍异常可追加
QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu --no-sandbox。