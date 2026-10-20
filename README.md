# MyChat

本地优先的微信聊天分析工具。界面接近微信桌面版，配合本地大模型完成关系判定、印象标签、对话建议和基于聊天记录的问答。

> 仓库不包含任何聊天数据、密钥或个人信息。请只分析你拥有并已获授权的数据，并遵守当地法律与平台规则。

## 功能

- **微信式界面**：导航栏 / 会话列表 / 聊天窗口，绿色选中态、气泡、时间分隔、群聊发送者、头像与图片。
- **右侧分析抽屉**：不打断聊天，随时打开「关系总览」或「当前聊天」。
- **AI 判定**：友善度、好感度、厌恶度、信任度、依赖度、情绪、印象标签、疑似关系与依据。
- **范围可选**：最近一个月 / 7 天 / 3 天 / 最近 50% / 全量历史；显示实际覆盖日期与条数。超长记录自动分段阅读再汇总。
- **对话指导**：贴入草稿并选择目标，生成多条可直接发送的回复、话题和注意事项。
- **问 AI**：基于统计数据、关键词检索和最近对话回答关于这段关系的问题。
- **批量分析**：按最近互动排序处理单聊，可随时停止，结果按「聊天 + 范围」缓存。
- **实时与回复**（可选）：监听微信数据库变化并自动追加新消息；通过辅助工具在微信界面中发送文本。

## 环境要求

- Python 3.10+
- 已解密的微信数据库，以及提供 `chats.py`、`config.py`、`image_decode.py` 的导出工具目录（可选提供 `wechat_send.py` 以启用发送）。
- 任意 OpenAI 兼容的本地模型服务，例如 LM Studio、omlx、vLLM。

本项目不包含微信解密或导出逻辑；数据准备依赖你自己的导出工具。通过 `WECHAT_TOOL` 指向该目录，并确保其中已有解密后的数据库与配置。

## 安装与启动

```bash
git clone https://github.com/imi4u36d/MyChat.git
cd MyChat

python3 -m venv .venv
.venv/bin/pip install -e .

export WECHAT_TOOL=/path/to/your/wechat-export-tool
./run.sh
```

打开 <http://127.0.0.1:8765>。

首次打开如果没有数据，在页面中点击同步，或直接运行：

```bash
WECHAT_TOOL=/path/to/your/wechat-export-tool .venv/bin/python -m mychat.dataset
.venv/bin/python -m mychat.stats
```

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8765` | 网页服务端口，仅监听 `127.0.0.1` |
| `WECHAT_TOOL` | `~/wechat-export-tool` | 数据导出工具目录 |
| `AI_BASE_URL` | `http://127.0.0.1:8000/v1` | OpenAI 兼容接口 |
| `AI_MODEL` | `qwen3.8flash` | 模型名；不存在时自动使用服务返回的第一个模型 |
| `AI_TIMEOUT` | `300` | 单次推理超时（秒） |
| `WECHAT_LIVE` | `1` | 是否监听新消息，设为 `0` 关闭 |
| `WECHAT_LIVE_POLL` | `3` | 轮询间隔（秒） |

示例：

```bash
AI_MODEL=your-model AI_BASE_URL=http://127.0.0.1:8000/v1 ./run.sh
```

## 使用

1. **同步**：左侧导航的刷新按钮把微信数据库整理为 `data/` 下的稳定数据集，并重建行为统计。
2. **选择聊天**：左侧列表支持搜索和单聊 / 群聊 / 全部筛选。
3. **打开分析**：点击聊天右上角的「AI 洞察」，从右侧滑出抽屉。
4. **选择范围**：在「当前聊天」页选择时间范围，点击开始判定。结果按范围缓存，新消息会标记过期。
5. **批量判定**：在「关系总览」中启动，按当前范围处理单聊；全量范围会先提示耗时。
6. **对话指导 / 问 AI**：抽屉内的另外两个标签页；输入框旁的「AI 起草」可把草稿带入指导。
7. **实时与回复**：页面底部状态显示实时连接；发送功能需要安装导出工具提供的辅助程序并在系统设置中授权。

## 数据与隐私

- 所有数据保存在本地 `data/` 目录，已被 `.gitignore` 排除，不会进入仓库。
- 服务只监听 `127.0.0.1`；模型请求只发送到配置的本地地址。
- 项目不包含遥测、上传或云端同步逻辑。
- `data/` 内是明文聊天记录，建议保持目录权限 `0700`、文件 `0600`，不要共享或上传。
- 截图、日志和导出的 JSONL/CSV 同样可能包含隐私，公开前请自行检查。

## 目录结构

```text
app.py                ASGI 入口（兼容 `uvicorn app:app`）
mychat/
  web.py              Starlette 路由
  service.py          同步任务、实时监听、缓存与消息读取
  dataset.py          微信数据 -> SQLite / JSONL
  stats.py            行为统计索引
  ai.py               本地模型调用、提示词、范围选择、缓存
  batch.py            AI 批量分析任务
  settings.py         环境变量与路径
  source.py           外部微信导出工具适配
  static/             前端（原生 HTML/CSS/JS）
tests/                纯逻辑单元测试
data/                 本地数据（不提交）
```

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/pytest -q
.venv/bin/ruff check .
```

CI 在 Python 3.10 / 3.12 上运行同样的 lint 和测试。

## API

服务启动后可访问：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/analysis` | 行为统计索引 |
| POST | `/api/sync` | 触发同步 |
| GET | `/api/chats/{username}/messages` | 消息列表 |
| GET | `/api/avatars/{username}` | 头像（`self` 表示自己） |
| GET | `/api/images/{md5}` | 图片消息 |
| GET | `/api/ai/status` | 模型连接状态 |
| GET | `/api/ai/tags` | 已缓存的 AI 判定摘要 |
| GET | `/api/chats/{username}/ai?scope=month` | 当前范围的分析缓存 |
| POST | `/api/ai/perception` | `{username, scope, refresh}` |
| POST | `/api/ai/guidance` | `{username, draft, goal, refresh}` |
| POST | `/api/ai/ask` | `{username, question, history}` |
| GET/POST | `/api/ai/batch` | 批量分析状态 / 启动 |
| POST | `/api/ai/batch/stop` | 停止批量分析 |

## 贡献

欢迎提交 issue 和 pull request。请勿在 issue、日志或截图中附带真实聊天记录、联系人或密钥。

## License

[MIT](LICENSE)
