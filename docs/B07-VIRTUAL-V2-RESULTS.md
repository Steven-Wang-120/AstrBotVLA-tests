# B07 独立虚拟闭环 v2 结果 — 2026-10-01

## 状态与结论

**已执行 20 个冻结公开合成任务 × 3 个完整 repeats × 2 臂，共 120 个
arm/task 尝试。** 独立 stdlib 三步状态机，不是硬件/Actor/production 测试。
任务成功由真实 validated 选项更新后的目标终态、绑定 identity、无运行命令及
停止证明判定，不是三次标签正确率相乘。正常、歧义、观测失效、冲突、失败恢复
各四任务；三个不可达/无法证明取消的任务每轮仍计入分母，不给保守操作假成功。

在该公开合成条件下，**LLM-only 更高成功率；hybrid 更快、减少 LLM 调用，但总
模型调用略多、官方用量估算不显示费用优势。** 不以此签署 Jev 可执行模式，
不调整 .6 confidence 门槛，不宣称未见 holdout，也不推论机器人效果。
Coordinator review 仍需审核协议、异常解释和边界。

## 实测对照

| 指标（每臂 60 次任务） | LLM-only | LLM planning + Jev shadow |
|---|---:|---:|
| 目标终态成功 | **51/60 = 85%** | **38/60 = 63.33%** |
| 三轮成功数 / 20 | 17 / 17 / 17 | 13 / 13 / 12 |
| 模型调用/预留尝试 | 228 | 237（其中 1 次发送未证明） |
| LLM 规划 | 60 | 60 预留，59 响应 |
| LLM 调度 | 168 | 0 |
| Jev 真实 HTTP 调度 | 0 | 177 |
| 请求 p50 / p95 / max | 945.03 / 1325.88 / 1581.80 ms | 453.00 / 1103.60 / 1424.50 ms |
| 任务 active 总时长 | **223.053 s** | **144.395 s** |
| 任务 active p50 / p95 / max | 3855.11 / 4474.28 / 4628.55 ms | 2414.21 / 2852.73 / 3927.20 ms |
| 当前官方价已知用量估算 | **$0.013483740** | **$0.014989614 已知小计** |
| 含未知调用的总估算 | $0.013483740 | **null** |
| 实际账单 | **null** | **null** |

请求延迟：LLM 含 HTTP 子进程启动/清理，Jev 含 adapter decide；global 限频等待
在任务 active 时长内，未计入请求延迟，quarantine 必须结束后才能下一次请求。
任务 active 时长包含恢复的原规划 RTT，排除中断/查文档的 idle gap；无法观测的
一个规划 RTT 为 null，未伪造延迟且排除 percentile。**非无中断原子实验**。
续跑 wall-clock 337.829 s；首个原请求到完成含中断查证 **641.878 s**。
两臂请求 deadline 30s LLM / 1500ms Jev 不同，不能解释为纯模型速度或实时保证。

### 分类成功数 / 各 12 次

| 类别 | LLM-only | hybrid |
|---|---:|---:|
| normal | 12 | 6 |
| ambiguity | 12 | 12 |
| observation_failure | 9 | 9 |
| conflict | 9 | 5 |
| failure_recovery | 9 | 6 |

LLM-only 失败：取消未证明 5、request_replan 1、终态未达到 3。
Hybrid 失败：中断规划无输出 1、终态未达到 21。Jev 177 个响应通过 adapter
格式校验，timeout 0；**43 次 confidence <.6 导致保守覆盖**。Jev 原始选择
cancel 29 / keep 12 / start 82 / wait 54；覆盖后 cancel 13 / start 82 / wait 82。
这些原始/后处理计数为语义诊断，不是额外执行或反事实成功数；未调门槛。
LLM 287 个可观测响应均有效、截断 0、timeout 0，另一个中断请求未获响应保留失败。

## 凭据、模型与中断披露

只读已有授权 CC Switch provider `4a5dc05f-ad51-437f-b137-60469993368c`；
使用其 in-memory key 与同 host 官方 Chat Completions URL，从第一个请求固定
`thinking:{"type":"disabled"}`、512 cap、JSON output、相同 prompts/guide/spec。
Jev 只读 Desktop `jev_api_keys_771.txt` **第一 token**，固定 `jev-1.13.0`。
无账号/key 轮换、无重试、无配置/权限放宽、无预算再询问。

**官方模型别名差异**：请求名 `deepseek-v4-flash`，所有 287 响应实际报
`deepseek-flash`。官方日志说明旧 V4 Flash 已退役并路由 V4.1 Flash。不能声称
响应固定为旧 V4；这不是 key/provider 切换。原 v2 错误采用 exact model 名相等
校验，在 34 次规划预留 / 33 响应 / 0 Jev 时停止。保留所有原始失败证据不覆盖，
新增 continuation 脚本使用已证明官方别名，33 个已有真实规划响应按 byte-identical
request SHA 复用，不再发请求。第 34 次未观测响应仍失败、仍算预算预留，发送状态
未知，**不能虚报为已证明真实 HTTP**。全部最高 288 LLM 预留 / 287 响应与 177
Jev HTTP，合计 465 尝试/预留记录，464 可观测响应，未重复计数两个目录的副本。

原 v2 exact-name 校验原始失败不删；**source interpretation 在初始 live 后改变**。
报告明确 source-before/after 与 correction；续跑自身 source SHA 稳定，原 v2
证据 byte-immutable；planner/selector prompt、cap、thinking、任务和 gate 无变化。
原父 capture 退出文件因 TaskStop 为空，非 exit0；另存 interruption-status。
continuation 原始 stdout/stderr/exit 为 **0**，无隐藏 live 错误。

## 用量、费用依据与预算

LLM-only：prompt 165414（cache hit 84480 / miss 80934），output 1817。
Hybrid LLM：59 个已知 planner prompt 27479（hit 15104 / miss 12375），output 708；
1 个未知用量不视为零。Jev 177 调用 input **301506**、output **8470**。

原 per-call/报告保留 CC Switch 旧配置 input .14/M / output .28/M /
cache .0028/M 的估算：LLM-only $0.012076064；hybrid LLM 已知 $0.0019730312。
**不是当前官方 V4.1 费率或 invoice**。新增 `official-price-supplement.json`
按本次所有 UTC 10:00 后调用的 off-peak 官方价算：DeepSeek input miss .15/M、
hit .003/M、output .6/M；Jev **input .042/M、output free**。Jev 只算 input
$0.012663252；原驱动保守把 output 也计费的小计 $0.013018992，额外
$0.000355740 明确为高估，原 artifact 未覆盖。Hybrid 当前官方已知合计
$0.002326362 + $0.012663252 = $0.014989614；未知一次调用的账单仍 null。

全 300 LLM 请求 configured reserve $0.71904，实际 288 reserve $0.6902784；
当前官方 off-peak 同 input 上界重算全 300 **$0.81648**、288 **$0.7838208**，
仍 ≤ $1。Jev 每次 65536 × .042/M，180 全预留 $0.49545216、177
**$0.487194624**。失败/未知调用不退预留。预算是保守估算，不是服务端消费限额。
官方 peak rate 全量保守预留会超 $1，**本脚本原配置预算不能直接用于 peak 时段
或未来费率；需 coordinator 新版本设计**。本次没有该时段调用。

## 检查与边界

- 最终 **25/25 v2 离线测试**（18 machine/transport、4 continuation、3 replay/
  tamper）通过；已有 12 v1 + 27 Jev 回归通过，总 **64**。CI 默认不读 key/不联网。
- 独立无联网 audit 重放 120 个任务，验证 planner 原始 JSON、每步 input/output
  请求关联、绑定与实际 state 转移、终态、调用计数、全部源与 v1 evidence SHA。
- v1 100×3 evidence 与 source/doc/corpus/core/B00 hashes 未改；不重跑 v1。
- SHA before/after：续跑 source 与原 interrupted evidence stable=true；v1
  immutable_evidence_unchanged=true。
- 精确 in-memory 双 secret 扫描：原 v2、continuation、raw checks **0 matches**；
  无 headers/full config/hidden reasoning/key 输出。HTTP 错误 body 不保留，Jev
  未知 string/ID 安全投影；不是声称 vendor raw bytes 原样持久化。
- Python 3.12.10 Windows 实跑；新增文件 Python 3.10 grammar 通过，但未在实际
  3.10/ARM64 实跑。无全项目 suite / hardware / production / commit / push。
- **边界例外需 review**：第一次离线 importlib loader 自动生成了 readonly
  sibling `scripts/__pycache__/evaluate_jev_live.cpython-312.pyc`（创建/写入 UTC
  11:03:07），它是自动缓存、不含 secret，非源文件改动。发现后立即把 loader
  改为 compile/exec，只读源码不再生成 sibling cache；没有擅自删除该缓存。
  Sibling 源 SHA unchanged。应由 coordinator 决定清理。除此无 D库/core/B00/
  sibling 写入。现有新增 `coordinator-evidence/B07-LLM-review` 非本 worker 创建，
  未改动。

## 新文件与交接

实现/审计：
- `scripts/b07_virtual_tasks_v2.py`
- `scripts/evaluate_virtual_closed_loop_v2.py`（原 exact-name 失败完整保留）
- `scripts/continue_virtual_closed_loop_v2.py`
- `scripts/capture_virtual_evidence_v2.py`
- `scripts/audit_virtual_closed_loop_v2.py`
- `scripts/finalize_virtual_evidence_v2.py`
- `scripts/seal_virtual_evidence_v2.py`（最终交付 SHA 与双凭据精确扫描）
- `tests/fixtures/decision/virtual-v2.tasks.json`
- `tests/test_virtual_closed_loop_v2.py`
- `tests/test_virtual_continuation_v2.py`
- `tests/test_virtual_audit_v2.py`
- `docs/B07-VIRTUAL-V2-PROTOCOL.md`
- `docs/B07-VIRTUAL-V2-CONTINUATION.md`
- `docs/B07-VIRTUAL-V2-RESULTS.md`
- `WORKER-PROGRESS.md` 仅追加 v2 状态，不改 v1 结果文档。

证据：`task-evidence/2026-10-01-llm-v2/` 原中断；
`task-evidence/2026-10-01-llm-v2-continuation/` 完整 report、每任务、每调用、
SHA、预算、replay、independent-audit、official-price-supplement、secret scans；
`task-evidence/2026-10-01-llm-v2-checks/` raw stdout/stderr/exit，初始错误也保留。
新 run 不允许 overwrite/retry；如需整洁无中断再实验必须新版本新预算，不能
偷偷重跑本次或 v1。

**MARINA threadId**：由 coordinator 最初 worker tool 返回持有，worker SDK 不
可见，不能伪造或用 local session 替代。实验 session `b07-v2-2aa261d79118`，
它**不是 threadId**。请通过 coordinator 原返回续接/review。结论待 coordinator
review，不签署 Jev execute/B07 全部或 B08 physical acceptance。

## 官方原始资料（Tavily 检索）

- https://api-docs.deepseek.com/guides/thinking_mode — explicit disabled
- https://api-docs.deepseek.com/api/create-chat-completion — Chat Completion schema
- https://api-docs.deepseek.com/guides/anthropic_api — 协议 endpoint 差异
- https://api-docs.deepseek.com/updates — Flash 退役与别名 routing
- https://api-docs.deepseek.com/quick_start/pricing — 当前 offpeak/peak tariff
- https://docs.typesafe.ai/models — pinned Jev、input-only price/output free
