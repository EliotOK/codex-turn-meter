# Codex Turn Meter 安装包

在 Windows 中完整解压后双击 `Install.cmd`，或运行 `python install.py`。
需要 Python 3.10+、Codex CLI，以及本机 Codex 自带的 plugin-creator 技能。
安装器将插件放到 `~/plugins/codex-turn-meter`，通过官方脚手架登记个人市场，
调用 CLI 安装。重复运行会更新同名插件并保留其他插件条目。

安装后新建任务，启用「用量 · 每轮更新」，输入「打开当前任务的用量面板」。
展开详情可按名称选择已有对话（只列最近 30 天内活跃的用户会话，最多 50 条）。
每条消息开始更新状态、结束更新数字。
插件 UI 需客户端提供 MCP App 渲染和工具交互能力。

源码和统计说明见 `codex-turn-meter/README.md`，本次测试范围见 `VALIDATION.md`。
运行后端测试：`python -m unittest discover -s tests -v`。
卸载：`codex plugin remove codex-turn-meter@personal`（若个人市场使用其他名称，应替换名称）。
卸载不删除源代码包；没有后台系统服务需要单独停用。
