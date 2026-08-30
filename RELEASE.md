# 发布流程（Release Guide）

本文档记录 DSH Desktop 从「改版本号」到「发布到 GitHub Releases」的完整流程，
基于 v1.0.2 的实际发布操作整理。

## 前置条件

| 工具 | 说明 |
|---|---|
| Python 3.10+ | 与运行时一致 |
| PyInstaller | pip install -r requirements-dev.txt（含 pyinstaller） |
| Inno Setup 6 | 默认安装路径 C:\Program Files (x86)\Inno Setup 6\ISCC.exe |
| Git + GitHub 凭据 | 已配置凭据管理器（git config --get credential.helper 应为 manager），用于 git push |
| GitHub CLI（gh） | gh auth status 显示已登录，token scope 含 repo（用于创建 Release / 上传资产） |

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

用 GitHub CLI（`gh`），**不要**用 `Invoke-RestMethod` 手拼 JSON 直接调 REST API。
v1.0.2 就是这么发的：Windows PowerShell 5.1 对字符串请求体的默认编码不是 UTF-8，
中文说明文字经过 `ConvertTo-Json` + `Invoke-RestMethod` 这一路会被吃成 `?`，
发布后 release notes 全是乱码，还是不可逆的（覆盖前的原文已经丢了，只能重写）。
`gh` 内部按 UTF-8 处理文件内容，不会有这个问题。

**发布说明必须先写成本地 UTF-8 文件，用 `--notes-file` 传入**——不要用
`--notes "一大段中文"` 或者拼 PowerShell 字符串塞进去，那样文字还是会先经过一次
控制台代码页转换，一样会乱码。

```bash
# 1. 用编辑器把更新说明写到一个 UTF-8 文件，例如 notes.md
#    （新增 / 修复 / 其他 三节，格式参考历史 release：
#     gh release view v1.0.2 --json body --jq '.body'）

# 2. 创建 Release，一次性带上所有资产（tag 需先 push 成功）
gh release create vX.Y.Z \
  "dist/DSH-Desktop.exe" \
  "installer_output/DSH-Desktop-Setup.exe" \
  "release/screenshot.png" \
  --title "DSH Desktop vX.Y.Z" \
  --notes-file notes.md
```

215MB 的大文件上传耗时数分钟，属正常现象，耐心等即可。

补充资产或者重新打包后要替换已上传的文件，用 `--clobber` 直接覆盖，不用先手动删旧的：

```bash
gh release upload vX.Y.Z "dist/DSH-Desktop.exe" --clobber
```

只改说明文字、不动资产：

```bash
gh release edit vX.Y.Z --notes-file notes.md
```

> `gh` 访问的是 api.github.com / uploads.github.com，如果遇到和「二、」里同样的
> DNS 污染连不上，用同一套「探测 IP → 写 hosts → 操作 → 恢复」的办法，
> 只是把 hosts 里的域名换成 api.github.com 或 uploads.github.com。

## 五、验证

```bash
# 1. Release 与资产齐全（screenshot.png / Setup.exe / exe 三个都在）
gh release view vX.Y.Z --json assets --jq '.assets[] | "\(.name)  \(.size) bytes"'

# 2. 发布说明确实是中文/UTF-8，不是乱码（本地落盘再看，避免终端本身编码问题）
gh release view vX.Y.Z --json body --jq '.body' > notes_check.txt
# 用 Read 工具或编辑器打开 notes_check.txt 核对

# 3. README 里的 latest 链接应返回 200（重定向到本版本资产）
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

**Q：gh release create 报资产已存在 / 上传失败要重传？**
A：用 `gh release upload vX.Y.Z <文件> --clobber` 直接覆盖，不用手动删旧资产。

**Q：release notes 里的中文变成了 `?`？**
A：说明又是从某个 PowerShell 字符串/heredoc 直接传给 `--notes` 或 API 的。
必须先存成 UTF-8 文件，用 `--notes-file` 传（见「四、」）。已经发出去的乱码
文本没法恢复原文，只能照实重写一份再用 `gh release edit vX.Y.Z --notes-file`
覆盖。

**Q：创建 Release 时 tag 不存在？**
A：必须先把 tag 推送成功（git push origin vX.Y.Z），再用 gh release create 创建。

**Q：hosts 写入失败（文件被占用）？**
A：安全软件（如 AlibabaProtect）会锁 hosts。用
[System.IO.File]::WriteAllText 全量写入可绕过；别用 Add-Content。

**Q：PyInstaller 打包后 WebEngine 打不开/卡死？**
A：main.py 已默认设置 QTWEBENGINE_DISABLE_SANDBOX=1；仍异常可追加
QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu --no-sandbox。