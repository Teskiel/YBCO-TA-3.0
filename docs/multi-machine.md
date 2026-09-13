# 多机协同规范

> 本文件是 **"多台电脑怎么不互相坑"** 的权威规范。
> 开发基线与模块细节见 [`../CLAUDE.md`](../CLAUDE.md)；机器清单见
> [`../machines/machines.json`](../machines/machines.json)；哪个版本从哪台机器上传见
> [`../sync/releases/INDEX.md`](../sync/releases/INDEX.md)。

## 1. 为什么要这套东西

本项目在 3 台以上电脑上开发与测量，代码靠 GitHub 同步。2026-09 的实测发现了
以下**已发生**的问题，不是假设风险：

| 现象 | 实证 |
|---|---|
| 本机落后远端 2 个提交而不自知 | 根 `CLAUDE.md` 曾写「HEAD `032d1a0`，工作区干净、已全部 push」，而 `origin/master` 已是 `666c658` |
| 同一份文档在两台机器各自演化 | `CLAUDE.md` 位于 `.gitignore` 之外却从未提交，两台机器各有一份 |
| 机器专属路径被提交进库 | `Noisesweep/gui_config.json`、`gui_config.py` 曾写死 `C:/Users/smlab/...` 与 `C:/Windows/System32/YBCO-TA-3.0/...`，而 README 声称「不依赖任何绝对路径」 |
| 版本标签无法追溯机器 | `v3.1` 原本是**轻量标签**（无 message），只能看到它指向哪个提交，看不出是哪台机器发布的 |
| `master` 上出现 merge 提交 | `666c658 Merge remote-tracking branch 'origin/master'`，说明当时是"先提交后拉取"而不是"先拉取后提交" |
| 提交者身份在机器间漂移 | 同一仓库的提交邮箱出现 `teskiel7@gmail.com` 与 `teskiel@users.noreply.github.com` 两种 |

这套规范把这六类问题各自变成**机制**，而不是靠记性。

## 2. 机器代号

代号是**稳定标识**：一经确定，**永不复用、永不改名**。它出现在 commit trailer、
版本标签 message、发布索引里；改名会让全部历史记录指向一个不存在的机器。

| 规则 | 说明 |
|---|---|
| 格式 | `^[a-z0-9][a-z0-9-]{1,23}$`（小写、可含连字符、2–24 位） |
| 命名 | `pc-<用途/人>` 或 `lab-<用户名>`，例如 `pc-teski`、`lab-smlab` |
| 登记 | 写进 [`../machines/machines.json`](../machines/machines.json)（入库） |
| 认机 | 每台机器跑**一次** `python sync/setup_machine.py --id <代号>` |

`setup_machine.py` 会在**仓库局部** git 配置里写入：

```
ybco.machine = <代号>            # 代号的权威来源
user.name / user.email           # 统一作者身份，消除邮箱漂移
```

为什么放在**仓库局部**而不是全局：作者身份是"人"的属性，跨机器应当一致；
机器身份是"环境"的属性，必须逐机不同。两者混在一起就没法查。

新机器入册：

```bash
python sync/setup_machine.py --add --id lab-pc2 \
    --label "实验机2" --role lab
# 然后提交 machines/machines.json（这是入库文件）
```

`--role` 取值：`dev`（开发机）/ `lab`（实验测量机）/ `archive`（归档机）。

## 3. 三个检查点

### 3.1 开工：先同步，再动手

```bash
python sync/check.py
```

它回答五个问题，并对 🔴 项给出可执行的补救命令：

1. **我是谁** —— 本机代号及这个认定的来源（`git-config` / `env` / `hostname`）
2. **我落后了吗** —— 与 `origin` 的领先/落后计数；落后时列出对方新增了哪些提交、出自哪台机器
3. **我漏交了吗** —— 未跟踪但"看起来该入库"的源码文件、stash、被改动的接缝文件
4. **我漏推了吗** —— 已提交但未推送的，逐条列出，缺 `Machine:` 标识的单独标出
5. **有什么风险** —— `master` 上的 merge 提交、库内残留的机器专属绝对路径等

落后时：

```bash
git pull --rebase          # 不要 git pull（会制造 merge 提交，见 §6）
python sync/check.py       # 复核
```

**铁律：动手改代码前必须先 `check.py`。** 落后状态下写出的改动，很可能是在
别人已经重构过的代码上做的，合并时才发现冲突——这是"进度不一"最主要的来源。

### 3.2 提交：机器标识自动补齐

装好钩子后（`python sync/setup_machine.py` 会装），每条提交自动带上：

```
feat: 新增 xxx 功能

正文……

Machine: pc-teski
Machine-Ref: DESKTOP-FJA4R3U/teski/Windows/py3.12.10
```

- `prepare-commit-msg` 钩子负责**补齐**（已存在则不动，尊重历史）
- `commit-msg` 钩子负责**校验**（缺失则拒绝提交，并打印补写方法）

为什么用 trailer 而不是别的：`git log --format=%(trailers:key=Machine)` 可直接
提取，机器信息独立成行，不污染正文。

合规的提交消息形如：

```
<type>(<scope>): <中文摘要，一句话说清改了什么>

<可选正文：为什么这么改、有什么取舍>

Machine: <机器代号>
Machine-Ref: <主机名>/<用户>/<系统>/py<版本>
```

`type` 取 `feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf`。

### 3.3 推送：门禁挡在 push 之前

`pre-push` 钩子会阻断这四种情况：

| 🔴 阻断项 | 为什么 |
|---|---|
| 落后于上游 | 你的推送建立在旧基础上，等于把别人的工作盖回去 |
| 有未提交改动 | 这些改动不会随 push 上传——**最常见的"忘了上传"** |
| 存在 stash | stash 不在任何提交里，换机器必丢 |
| 提交缺 `Machine:` 标识 | 无法追溯是哪台机器做的 |

🟡 仅提示：未跟踪的源码类文件、命中接缝文件、单次推送积压过多。

紧急绕过：

```bash
YBCO_SKIP_HOOKS=1 git push
```

绕过会打印醒目留痕，并且 `check.py` 会把 `YBCO_SKIP_HOOKS` 已设置这件事
列为 🟡 提示——**别让它变成常态**。

### 3.4 收工：确认没有留下未上传的东西

```bash
python sync/check.py
```

全绿再关机。"今天改的东西在哪"应该永远有答案。

## 4. 发布版本

```bash
python sync/release.py --bump patch --message "一句话摘要"
```

它会：

1. 前置校验：工作区干净、与上游同步、待推送提交都有机器标识（任一不满足就拒绝）
2. 创建**附注标签**（annotated），message 形如：

   ```
   YBCO-TA 3.2.0 — 一句话摘要

   Machine: pc-teski (DESKTOP-FJA4R3U/teski/Windows/py3.12.10)
   Repo:    D:/code/project/YBCO-TA-3.0
   Commit:  <sha>
   Date:    2026-09-13T15:00:00+08:00
   ```

3. 更新 `VERSION` 文件（版本号唯一来源）
4. 追加 [`../sync/releases/INDEX.md`](../sync/releases/INDEX.md) 与 `log.jsonl`
5. 推送标签与分支

**版本号只增不改**：已经发布过的版本号不能再指向新的提交。要改已发布的内容，
发一个新版本。标签一旦推送就不要移动——移动标签会让其它机器上的引用失效。

### 历史版本的机器归属

3.0 / 3.1 早于本框架，提交里没有 `Machine:` trailer。**不能为了补 trailer 去改写
历史**：`git filter-branch` / rebase 会改变所有后继提交的 SHA，导致既有标签失效、
其它机器的 clone 分叉、文档里的 SHA 引用全部作废。

因此历史归属只能**事后登记**在一份人工映射里：
[`../machines/history.json`](../machines/history.json)。每条记录都带
`confidence`（`high`/`medium`/`low`）与 `evidence`（判定依据），生成总表：

```bash
python sync/map_history.py        # 写 docs/machine-map.md
```

已生成的按机器分组的提交总表见 [`machine-map.md`](machine-map.md)。
**推定要标成推定，不要把猜测写成事实。**

## 5. 配置分层：什么该入库，什么不该

这是"换台电脑就跑不起来"的根因，也是本规范最需要记住的一节。

### 5.1 五类内容

| # | 内容 | 存哪 | 例 |
|---|---|---|---|
| ① | 逻辑、算法、文档、测试 | **入库** | `Data_process/_lib/thermal_model.py` |
| ② | 机器路径、仪器地址 | `*.machine.json`（**不入库**） | `Noisesweep/gui_config.machine.json` |
| ③ | 原始测量数据与产物 | **不入库**（`.gitignore`） | `*.s2p`、`*.h5`、`output/` |
| ④ | 私钥、口令、token | **永不入库** | `~/.ssh/id_*` |
| ⑤ | 一次性实验脚本 | **先入库再说** | 临时分析脚本 |

第 ⑤ 条看似反直觉，但"扔在本地的一次性脚本"正是最常丢的东西。先提交，
大不了之后再删。

### 5.2 分层命名约定

```
foo.json                  入库   基准/示例值，必须对任意机器都成立
foo.machine.json          不入库 本机的真实值（.gitignore 已排除）
foo.machine.example.json  入库   模板，新机器照抄
```

加载顺序（`sync/machine_config.py` 实现）：
**环境变量 → `*.machine.json` → `foo.json` → 代码内默认值**。

**硬约束**：任何一层缺失都**不能抛异常**，只能回退并打印一行提示。因为离线
自检链路（`Noisesweep/_verify_*.py`、`--dry-run`）必须在"全新 clone、什么都没配"
的机器上也能跑通。

已分层的配置：

| 基准（入库） | 本机覆盖（不入库） | 加载器 |
|---|---|---|
| `Noisesweep/gui_config.json` | `Noisesweep/gui_config.machine.json` | `sync/machine_config.py` |
| `Auto_Sweep/app_settings.example.json` + `app_settings.json` | `Auto_Sweep/app_settings.machine.json` | `Auto_Sweep/app_settings.py` |
| `Noisesweep/noisesweep_config.json` | `Noisesweep/noisesweep_config.machine.json` | CLI 显式传 `--config` |

环境变量逃生阀：`YBCO_GUI_CONFIG`、`YBCO_APP_SETTINGS`、`YBCO_MACHINE`、
`YBCO_DRAW_OUTPUT_BASE`、`YBCO_S21_HDF5`、`YBCO_PYTHON`。

### 5.3 接缝文件（多机共写）

以下文件存放的是**运行时状态**而非源码，两台机器同时改就会冲突或互相覆盖。
`sync/check.py` 在你改动它们时会给出 🟡 提示：

- `Data_process/resonance_table.txt` —— 谐振追踪表，测量时追加
- `Auto_Sweep/presets/*_default.json` —— 仪器预设
- `Auto_Sweep/app_settings.json` —— 已把机器专属的 `addresses` 拆到机器层

改这些文件前后各跑一次 `sync/check.py`；能写进机器层就别写进基准文件。

## 6. 冲突处理铁律

**`master` 上只做 `pull --rebase` + 快进推送，禁止制造 merge 提交。**

```bash
git pull --rebase        # 正确
git pull                 # 会制造 merge 提交；666c658 就是这么来的
```

需要并行开发时开分支，分支名带机器代号：

```bash
git switch -c feat/lab-smlab-noise-orchestrator
```

**不要 force-push `master`。** 别的机器可能已经基于它做了提交。

**不要改写已推送的提交。** `git rebase` / `commit --amend` 只对未推送的提交安全。
已经推上去的提交要修，用新的提交去修。

## 7. 数据备份（当前最大的盲区）

`.s2p` / `.h5` / `.pkl` 全部被 `.gitignore` 排除。这意味着**原始测量数据只存在于
产生它的那台机器上，没有任何版本与备份**。硬盘坏一次就没了。

建议（本规范只定约定，具体介质由你决定）：

1. **路径一致**：每台机器上的原始数据根目录用**同一个相对结构**，例如都是
   `<数据根>/<chip_id>/<run_id>/{T}K/`。这样备份脚本与清单跨机通用。
2. **清单入库**：每批测量后追加一份 `data_manifest.csv`（不含数据本体）：

   | 列 | 含义 |
   |---|---|
   | `measured_at` | 测量时间 |
   | `machine` | 机器代号 |
   | `chip_id` / `run_id` | 芯片与轮次 |
   | `temperatures_k` | 温度点列表 |
   | `file_count` / `total_bytes` | 文件数与总大小 |
   | `md5_sample` | 抽样校验和 |

   数据本体不入库，但**"数据存在哪里、有哪些"必须可查**。
3. **异地一份**：外置硬盘或 NAS，定期同步。别把唯一副本放在实验机上。

## 8. 安全

- 仓库当前是 **PUBLIC**（`Teskiel/YBCO-TA-3.0`）。`sync/check.py` 会把库内的
  机器专属绝对路径列为 🔴——它们既让代码换机器即坏，也泄露目录结构。
- `machines/machines.local.json` 与 `*.machine.json` 会含本机路径与仪器地址，
  **永不入库**（`.gitignore` 已排除，`check.py` 也会在误 `git add` 时报警）。
- 如需更高保险，可把仓库转为 private：`gh repo edit --visibility private`。

## 9. 已部署的集群化注意事项

| 事项 | 说明 |
|---|---|
| 实验机的克隆位置 | 曾部署在 `C:\Windows\System32\YBCO-TA-3.0`。该目录需要管理员权限，且受 UAC 文件虚拟化影响（读写可能被悄悄重定向到 VirtualStore）。**建议迁到 `C:\Users\smlab\` 或 `D:\`。** |
| SSH 引导 | `setup.bat` 曾只在 `~/.ssh/id_ed25519` 不存在时才生成密钥，却不检查已有 `Host github.com` 块用的是哪个 `IdentityFile`——于是会生成一把**用不上**的新密钥并误判认证失败。已改为：先用 `ssh -T git@github.com` 试探既有配置，通过就不再插手。 |
| Python 版本 | 各机器可能不同（本机 3.12.10，实验机 3.14）。`machines.json` 逐机登记，便于排查版本相关差异。`Noisesweep/启动.bat` 不再写死解释器路径，改为 `YBCO_PYTHON` → `python` → `py -3` 依次探测。 |

## 10. 命令速查

```bash
# 每台机器只需一次
python sync/setup_machine.py --id <代号>          # 认机 + 装钩子
python sync/setup_machine.py --show               # 查看本机认定状态

# 开工 / 收工
python sync/check.py                              # 体检（人类可读）
python sync/check.py --json                       # 机读
python sync/check.py --strict                     # 有 🔴 时退出码 1
python sync/check.py --offline                    # 不联网

# 发布
python sync/release.py --bump patch --message "…"
python sync/release.py --version 3.2.0 --dry-run

# 追溯
python sync/map_history.py                        # 生成 docs/machine-map.md

# 钩子管理
python sync/install_hooks.py --status
python sync/install_hooks.py uninstall

# 配置诊断
python sync/machine_config.py Noisesweep/gui_config.json
python Auto_Sweep/app_settings.py

# 守护测试（不需要硬件与网络）
python -m pytest sync/tests -q
```

## 11. 已知的环境限制

在受限沙箱（例如 AI agent 的执行环境）里，Git 的钩子是 `sh` 脚本，而某些沙箱
不允许 `sh.exe` 创建信号管道，症状是：

```
sh.exe: *** fatal error - couldn't create signal pipe, Win32 error 5
```

此时提交会被拒绝，但**不是钩子逻辑的问题**。在这种环境里：

- 直接跑 Python 逻辑本身：`python sync/trailer.py --check <消息文件>`
- 直接跑推送门禁：`python sync/pre_push.py --check-only --range origin/master..HEAD`
- 或临时绕过：`YBCO_SKIP_HOOKS=1 git commit ...`（有留痕）

**在正常终端里钩子工作正常。** 同理，`Noisesweep/_verify_*.py` 会向临时目录
创建以下划线开头的子目录，某些沙箱会拒绝该操作——请在有完整写权限的终端中运行。

### 受限环境下 `Auto_Sweep/tests` 会大面积报错

在无法创建/清理 pytest 临时目录的沙箱里，`python -m pytest Auto_Sweep/tests`
会出现 100+ 个 `PermissionError: [WinError 5] ... 'tmpXXXX'`，看着像天塌了，
其实**与代码无关**——这些测试用 `tmp_path`，而沙箱不让建/清临时目录。

正确基线（在有完整权限的终端里跑）：

| 数量 | 说明 |
|---|---|
| 约 645 通过 | 正常 |
| 11 失败 | 本机没装 NI-VISA（缺 `visa32.dll`），环境缺失 |
| 约 29 失败 | `draw/plot_VNA_powersweep.py` 的 `from _backend import ...`，既有 bug |

判断"我这次改动有没有引入回归"，**看的是这个基线有没有变坏**，而不是"是不是全绿"。
`python -m pytest sync/tests -q`（35 项）不依赖临时目录、不接硬件、不联网，
**在任何环境都应当全绿**——它才是本框架给出的硬信号。
