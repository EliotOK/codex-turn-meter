---
name: turn-meter
description: 打开或查询 Codex 当前任务的上下文、缓存命中和 token 用量面板。用户要求用量面板或每轮用量统计时使用。
---

# Turn meter

Call `show_meter` once with the exact current task ID when it is known from trusted task context.
If the ID is unavailable, omit it and let the user select a task inside the panel.
Never substitute the most recently active task. Never read conversation contents to infer an ID.

The MCP App handles subsequent updates through UI-only tool calls. Do not add repeated
model-side tool calls, automations, hooks, or messages to refresh it. Do not call
`wait_for_turn` from the model. The current open panel must remain mounted for updates.

For a one-time text query use `read_meter` with an exact ID. Clearly distinguish
last request, current turn and cumulative accounting. Unknown values remain unknown.
Context occupancy is estimated from last reported input; cumulative tokens are not
context occupancy or remaining subscription quota. Output includes reasoning output.

If the host cannot render the MCP App, report the limitation and provide the numeric
result when requested. Do not claim that a text result proves embedded rendering.
