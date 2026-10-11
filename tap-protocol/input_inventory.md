# 输入清单（G0/G1）

| 路径 | 角色 | 权威性 | 备注 |
|---|---|---|---|
| `CLAUDE.md`（根，7466 B） | 项目权威开发基线 | **唯一权威**（但其测试数字已过期，见 DOC-001） | 含多机协作、配置分层、7 条陷阱、不要读清单 |
| `Auto_Sweep/CLAUDE.md`（17091 B）+ `draw/` `tests/` `ui/` 三份 | 模块级细节 | 权威 | 本轮未逐字读取（按其中「不要读」指引省 token） |
| `Data_process/CLAUDE.md`（8235 B） | 离线数据处理 | 权威 | **无 tests**，验证须靠实跑数据 |
| `Noisesweep/CLAUDE.md`（7187 B） | 噪声测量编排 | 权威 | 平铺模块，运行时 cwd 必须是该目录 |
| `machines/machines.json` | 跨机协作的唯一权威机器清单 | 权威 | 3 条记录，2 条含「待确认」 |
| `VERSION` = 3.1.1 | 版本号唯一来源 | 权威 | |
| `requirements.txt` / `install.py` / `setup.bat` | 三处部署件须一致 | 权威 | 本轮未做一致性核对（属 G2） |
| `sync/check.py` / `sync/tests` | 多机同步防线 | 权威 | 实测 54 项 |
| `Noisesweep/_dryrun_config.json` | 免硬件 dry-run 配置 | 权威 | 实测 exit 0 |
| `docs/multi-machine.md` | 多机规范全文 | 参考 | 18 KB，本轮未读 |
| `../YBCO_TA/`、`scraps`、`scarp`、`YBCO-TA-data` | 旧库与数据仓 | 参考 | 本轮未动 |

**只读声明**：本轮对上述所有文件**只读**；`git status` 在每步之后都应为干净。
