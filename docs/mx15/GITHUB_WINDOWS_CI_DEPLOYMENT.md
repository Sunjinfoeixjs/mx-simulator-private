# MX-15 | GitHub Windows 自托管 CI 与合并前强制检查

## 当前交付与部署状态

**代码已在本地独立工作树准备；尚未部署到 GitHub。** MX 本机仓库 `C:\Users\Administrator\MX-mx15` 当前无 Git remote。已连接 GitHub 账户仅找到 `q505053132-sys/64646`，这是**公开且只有 README 的另一个仓库**，不可擅自当成 MX 模拟服，更不能在其中注册可以执行 PR 代码的家用/开发机 Runner。GitHub 连接器不能访问需要仓库 Administration(write) 的保护规则 API（403），Windows 没有 `gh` CLI，也没有 Runner 服务。需要先确定一个**确实属于 MX 项目的私有仓库**。

## 已准备的文件

- `.github/workflows/mx15-windows-required.yml`：`pull_request` 与 `push`（目标 main/master）；先在 GitHub-hosted Linux Job 核对仓库必须私有、PR 必须来自同一仓库，再允许 `[self-hosted, Windows, X64, mx-ci-windows]` Runner 跑测试。
- `.github/scripts/run-mx15-windows.ps1`：校验冻结 P0 `15dd282` 和外部 P67 `7db4df1`，把独立可信 Git P67 只读源克隆成一次性工作目录；仅复制外部客户端 `*.py` 到已被 Git 忽略的工作目录（不复制原始账号配置、INI、DLL 或客户端二进制）；运行 `selftest`、`lint`、`incremental`、`full`。任一脚本非零退出，整个必需 Check 失败。
- `.github/mx15-ruleset-template.json`：默认分支 active ruleset，PR、禁止强推与删除、强制最新基线以及两个必需 Check。
- `.github/scripts/apply-mx15-ruleset.ps1`：默认为只读 dry-run。仅对输入的正确私有仓库配合管理员登录的 `gh` 执行 `-Apply`：校验远端工作流存在、Runner `mx-ci-windows` 在线且无同名规则，才提交规则。不会获取或打印 Token，也不会直接修改不明仓库。

## 先建立私有 MX 仓库

1. 用有管理权限的 GitHub 账户创建或确认 **private** 的 MX 项目仓库（和当前公开的 64646 不同），核对它是正确的 MX Git 基线。严格审查 2,600 余个跟踪文件是否有私钥、账号、用户数据、大型受版权限制素材和不宜公开的资产。
2. 在正确的私有仓库设置 Git remote 并按审查结果推送 MX 相关分支及冻结的 P0 tag。**禁止为了部署 CI 而修改 `p0-accepted` 的指向**；实际默认分支要能找到 P0 冻结 commit，`git merge-base HEAD p0-accepted` 必须正确。不要让 GitHub 自动创建空仓库 README 初始提交成为不相关历史。
3. 将本分支中的 `.github/workflows/mx15-windows-required.yml` 及脚本提交至私有仓库中受保护的默认分支，才能从 PR/Push 自动触发。不要使用 `pull_request_target` 运行未受信任代码。

## 配置独立的 Windows Runner（必须是专用隔离 VM）

1. **禁止使用当前有游戏客户端、源代码和个人账号的开发桌面，尤其禁止注册到公开仓库。** 使用独立 Windows x64 虚拟机（推荐每轮重建），安装并固定受信任的 Git for Windows、Python、PowerShell 7（`pwsh`）与 Actions Runner。Runner 使用最小权限专用账户运行，网络范围限制；不在 Runner 中存储 GitHub 账号 Token/SSH 私钥。
2. 在虚拟机中由管理员预置两个**经审查可信、不可供 Pull Request 任意写入的来源**（下面是示例路径，可按实际选择）：
   - `C:\MX-CI\P67-frozen`：MX 的完整 Git 项目克隆，HEAD 必须是 `7db4df1` 且其 `p0-accepted` 标签属于同一个仓库；`mx13_runs` 下的 P67 原始证据须可读。
   - `C:\MX-CI\ClientSnapshot\src`：已经审批的客户端 Python 源快照，`net.py` 存在，且 MX-12 的 `--verify` 测试能验证与冻结的 SHA256 一致。不要使用游戏客户端真实账户目录。
3. 在 **GitHub 正确私有仓库 → Settings → Secrets and variables → Actions → Variables** 创建两个非密钥变量：`MX_CI_P67_REPO=C:\MX-CI\P67-frozen` 与 `MX_CI_CLIENT_SRC=C:\MX-CI\ClientSnapshot\src`。变量只是本机文件路径，**不要填入任何密钥或个人目录的真实口令**。
4. 在 **Settings → Actions → Runners → New self-hosted runner → Windows x64** 运行 GitHub 生成的最新下载安装和配置命令，并添加唯一标签 `mx-ci-windows`。一次性注册 token 由 GitHub UI 生成，会过期；**不要把 token 发到聊天、日志、仓库或 PR**。在隔离 VM 内把 Runner 作为专用账户的服务运行；确认 Runners 中状态为 `Online`。
5. 通过独立 Windows VM PowerShell 做只读预检：

```powershell
$env:MX_CI_P67_REPO = 'C:\MX-CI\P67-frozen'
$env:MX_CI_CLIENT_SRC = 'C:\MX-CI\ClientSnapshot\src'
& '.\.github\scripts\run-mx15-windows.ps1' -RepoRoot (Get-Location).Path -ValidateOnly
```

测试门禁在一次性 GitHub job workspace 运行，不要共享 Runner 的 `_work` 目录给其它私人项目。作业最终上传的只有机器化 JSON 验收元数据（保留 14 天），不上传原始 stdout/stderr、外部客户端、游戏存档、用户口令等。结束时 `if: always()` 删除暂存的客户端及 P67 工作副本。Runner 本身如果未隔离重置仍有持久化风险，生产建议使用每作业重新建立的 VM。

## 先验证第一次真实 GitHub PR

在私有仓库创建受控测试 PR，保证 GitHub Actions 列表出现工作流 `MX15 Windows Required Gates`。至少看到下列两个**不同的 Check**：

- **`mx15/trusted-repository`**：运行 GitHub-hosted Linux，拒绝公开仓库和 fork PR。
- **`mx15/windows-full`**：只在可信检验通过后调度匹配 `mx-ci-windows` 的 Windows VM，依次执行 MX-15 `selftest + lint + incremental + full`，必须实际完成 **65/65** 和单机连续两次成功。任意日志/分派/S2C 漂移或非零退出都使 Check 失败。

首次真正运行后，以 GitHub PR Checks 中显示的确切名称为准。如果使用了不同的显示名，不要盲目设置 Ruleset 中不存在的 Check；先修正名称并再次成功运行。等待中的 Runner `queued` 应当挡住合并，不允许通过跳过 Job 绕过。

## 设置合并前必需 Check

管理员在 **Settings → Rules → Rulesets → New branch ruleset**，匹配 `~DEFAULT_BRANCH`，启用以下规则：Require a pull request before merging、Require status checks to pass、Require branches to be up to date、禁止 force push 和删除。设必需 Checks 为 **`mx15/trusted-repository`** 和 **`mx15/windows-full`**，来源优先明确选择 GitHub Actions。如果有第二位审核员，推荐代码所有者审查；当前只有一个维护者时先不要强制没有人能完成的多人审批。

配置已准备为 `.github/mx15-ruleset-template.json`。管理员可在已安装、已授权 `gh` 的终端中验证再执行：

```powershell
& '.\.github\scripts\apply-mx15-ruleset.ps1' -Repo 'OWNER/PRIVATE_MX_REPO'
& '.\.github\scripts\apply-mx15-ruleset.ps1' -Repo 'OWNER/PRIVATE_MX_REPO' -Apply
```

`-Apply` 需要仓库管理写权限；此脚本会先检查仓库私有、远端工作流已落地、Runner 带正确标签且 Online，并拒绝创建同名的重复规则。**当前未实际运行 -Apply，也未在任何 GitHub 仓库启用保护规则。**

## 必须复验的失败场景

- 将工作流误部署至公开仓库：`trusted-repository` 必须 FAIL，Windows 不调度。
- 外部 fork PR：可信检查 FAIL，Windows 不调度。
- 私有 Runner 离线/错标签：Windows Check 必须 pending，不允许合并。
- 严格 JSON 含重复键、变更分派逻辑、S2C 冻结数据漂移或 65 项任何一个失败：`mx15/windows-full` 必须 FAIL。
- 原生 GUI 解析证据缺失时，MX-15 **代码合并门禁**仍可通过，但独立 `release` 门禁必须 FAIL / `NOT_TESTED`；不可把代码回归 PASS 当成 MX-25 的原生客户端验收或 P8 原服真实性 PASS。
- 重新签发 P0 tag、强推默认分支、直接绕过必需检查应受到 Ruleset 保护。

## 当前明确阻断

本机没有 GitHub remote、没有 `gh` CLI，也没有自托管 Runner 服务；连接器只能读取公开占位仓库，尝试读取保护规则返回 403。因此无法核实或执行正确 MX 私有仓库上的 Runner 注册、首次 PR Check、规则启用和远程合并阻断。**本轮只能认定本地工作流和安全配置已创建、只读预检 PASS，不能宣称 GitHub 端已启用。**
