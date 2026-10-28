# 开发与版本记录

本项目计划以 GitHub 组织 `YSZN-Ainvest` 下的 `Qsim` 私有仓库作为代码更新入口。只有实际完成远端创建/推送后，GitHub 才拥有本地提交的备份；本地 commit 本身不代表已经上传。

## 仓库范围

提交 Python 源码、配置模板、说明文档、测试和 CI 配置。NAS 原始数据、复权因子、回测分区、运行结果、日志和本机验证清单不进入 Git。实际数据根目录通过配置指定，生成结果保留在 NAS。

`.gitignore` 是误提交保护；提交前仍要检查 `git diff --cached --stat` 和 `git diff --cached`。自定义本机配置可保存为 `configs/local*.toml`，该模式已忽略。

## 首次安装

在克隆出来的仓库中，使用 Python 3.11 或以上版本：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

30 项单元和集成测试使用构造数据及临时文件，不要求挂载 NAS。真实数据回测需另行配置 `data.root`、`data.raw_root` 和输出位置，并拥有相应文件访问权限。示例配置中的服务器路径是部署示例，不是仓库附带的数据。

`run.sh` 优先使用 `QSIM_PYTHON` 指定的解释器，其次使用本机已有 alpha 环境，其他机器回退到 `python3`。也可直接运行 `python -m qsim --config ...`。

## 日常迭代

建议保持 `main` 为可运行版本，每个功能或修复单独分支和提交。例如：

```bash
git switch main
git pull --ff-only
git switch -c feature/factor-interface

# 修改代码及相关测试
python -m unittest discover -s tests -v
git add qsim tests configs README.md STATS.md CHANGELOG.md
git diff --cached --stat
git diff --cached
git commit -m "feat: extend point-in-time factor interface"
git push -u origin feature/factor-interface
```

通过 Pull Request 说明问题、行为变化与验证结果，再合并回 main。小修复可使用 `fix:`，文档使用 `docs:`，不改变行为的整理使用 `refactor:`。这只是本项目约定，不依赖额外 Git 工具。

影响收益、成交、时点可见性或统计定义的改动应同步更新 `STATS.md` 和 `CHANGELOG.md`，并增加能区分改动前后行为的测试。历史回测目录保持原样；新的结果写新目录，以保留当时的配置及代码指纹。

## 发布版本

稳定版本同步修改 `pyproject.toml` 和 `qsim/__init__.py`，测试通过后创建带说明的版本标签并单独推送，例如 `v0.2.0`。标签记录代码快照；不自动包含 NAS 数据或回测结果。

仓库不预设开放源代码许可，默认按内部项目管理。是否公开及采用何种许可证需由项目所有者决定。
