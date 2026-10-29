# 开发说明

仓库：[YSZN-Ainvest/Qsim](https://github.com/YSZN-Ainvest/Qsim)。

开始开发先读 [开发交接文档](document/QSIM_DEVELOPMENT.md)。每次迭代同步更新当前状态并追加改动、验证和未完成项；具体维护要求见 [AGENTS.md](AGENTS.md)。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

测试使用构造数据，不需要 NAS。真实回测需配置数据路径和输出位置。

## 提交

`main` 保持可运行，功能和修复分别建分支：

```bash
git switch main
git pull --ff-only
git switch -c feature/example

# 修改代码，运行相关测试
git add <files>
git diff --cached
git commit -m "feat: describe the change"
git push -u origin feature/example
```

PR 写清改了什么、为什么改、如何验证。涉及收益、成交或时点规则的改动，同步更新 `STATS.md` 和测试；版本变化写入 `CHANGELOG.md`。

仓库只提交代码、配置、文档和测试。数据、回测结果、日志及凭据不提交。本机配置可命名为 `configs/local*.toml`，已加入忽略规则。

## 版本

发布时同步修改 `pyproject.toml` 和 `qsim/__init__.py`，测试通过后创建版本标签。回测结果写新目录，保留当时的配置和代码指纹。
