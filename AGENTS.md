# QSim 开发交接

默认在 `main` 维护和交付最新版，避免为普通迭代累积功能分支。确需临时分支时，验证完成后合回 `main` 并同步远端；不要让用户长期到不同分支寻找最新版。

开始修改前先读 `document/QSIM_DEVELOPMENT.md`，再按任务阅读 `STATS.md` 和相关实现。工作区 `/home/qinml/AGENTS.md` 的目录与资源限制仍适用；交接文档也保存了这些要求，方便其他环境接手。

每次完成 QSim 的代码、配置、数据接口或统计口径迭代，必须同步维护 `document/QSIM_DEVELOPMENT.md`：更新当前状态，在开发记录中追加目标、改动、验证、兼容性影响和未完成项。失败或中断也记录已完成部分和恢复位置。不要把未执行的测试写成通过。

涉及交易或统计定义时同步更新 `STATS.md`；版本变更更新 `CHANGELOG.md`。用户说明保持简洁，AI 交接文档可以保留必要的实现细节和证据路径。

交接文档只有一个维护版本：仓库内 `document/QSIM_DEVELOPMENT.md`。本机 `/home/qinml/document/QSIM_DEVELOPMENT.md` 是它的软链接，不另存副本。不要在文档中写入凭据或大段运行日志。
