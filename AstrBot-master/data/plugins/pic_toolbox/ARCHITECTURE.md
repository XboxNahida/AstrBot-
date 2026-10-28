# 🏗️ 架构文档 (ARCHITECTURE.md)

> astrbot_plugin_pic_toolbox v1.2.0 — 插件内部设计与数据流说明

---

## 目录

1. [整体概览](#1-整体概览)
2. [插件生命周期](#2-插件生命周期)
3. [指令路由与解析](#3-指令路由与解析)
4. [图像处理管道](#4-图像处理管道)
5. [GIF 帧展开与保存](#5-gif-帧展开与保存)
6. [表情包生成器](#6-表情包生成器)
7. [图片来源优先级](#7-图片来源优先级)
8. [文件生命周期与清理](#8-文件生命周期与清理)
9. [配置系统](#9-配置系统)

---

## 1. 整体概览

```
┌─────────────────────────────────────────────────────┐
│                    AstrBot 消息                      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  handle_all_commands()                               │
│  ├─ 解析 @ 目标用户                                  │
│  ├─ 提取指令文本 (去除 / 前缀)                        │
│  └─ 匹配模式守卫 (match_mode / / 前缀)               │
└─────────────────────┬───────────────────────────────┘
                      │
         ┌────────────┼────────────┬─────────────┐
         ▼            ▼            ▼             ▼
    ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌───────────┐
    │ 变换类  │ │ 对称类  │ │ 表情包类 │ │ 调速      │
    │ 反色    │ │ 左对称  │ │ 摸头/发射│ │ 调速 N×   │
    │ 顺时针  │ │ 右对称  │ │ 杀/操你  │ │           │
    │ 逆时针  │ │ 上对称  │ │ 抽你     │ │           │
    │ 左右翻转│ │ 下对称  │ │          │ │           │
    │ 上下翻转│ │         │ │          │ │           │
    └────┬────┘ └────┬────┘ └────┬─────┘ └─────┬─────┘
         │           │           │              │
         ▼           ▼           ▼              ▼
    ┌─────────────────────────────────────────────────┐
    │  _download_and_process / _dual_avatar            │
    │  └─ 下载 → 处理 → 返回 → 异步清理                  │
    └─────────────────────┬───────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────────┐
    │  meme/ 模块                                      │
    │  ├─ gif_utils.py  ← GIF 帧展开 & LZW 保存        │
    │  ├─ invert.py     ← 反色                         │
    │  ├─ flip.py       ← 水平 / 垂直翻转              │
    │  ├─ rotate.py     ← 顺时针 / 逆时针旋转           │
    │  ├─ mirror.py     ← 四方向对称                    │
    │  ├─ gif_speed.py  ← GIF 调速                     │
    │  ├─ petpet.py     ← 摸头杀生成器                  │
    │  ├─ shoot.py      ← 发射表情包 (含人脸检测)       │
    │  ├─ do.py         ← 撅人 (双头像)                 │
    │  ├─ lash.py       ← 鞭笞 (双头像)                 │
    │  └─ behead.py     ← 砍头 (单头像)                 │
    └─────────────────────────────────────────────────┘
```

---

## 2. 插件生命周期

### 初始化 (`__init__`)

```python
class PicToolboxPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        self._enable_at_avatar = config.get("enable_at_avatar", True)
        self._match_mode = config.get("match_mode", False)
        self._gif_speed_allow_frame_drop = config.get("gif_speed_allow_frame_drop", False)
        self._cleanup_stale_tempfiles()  # 清理崩溃残留
```

- `enable_at_avatar`：是否允许 `@用户` 取头像
- `match_mode`：精准匹配模式开关
- `gif_speed_allow_frame_drop`：调速丢帧开关
- 启动时自动清理超过 1 小时的残留临时文件

### 销毁 (`terminate`)

```python
async def terminate(self):
    pass  # 预留，暂无清理逻辑
```

> 临时文件依赖异步 10 秒延时清理 + 启动时的过期清理，不在 terminate 中处理。

---

## 3. 指令路由与解析

### 3.1 入口：`handle_all_commands`

所有指令通过单一入口 `handle_all_commands()` 处理，手动解析消息文本、@ 目标和图片 URL。

### 3.2 指令文本提取

```
原始消息: "/顺时针 @123456" 或 "@123456 顺时针"
           ↓
1. 判断是否包含 " @" → 去掉 @ 部分取前面的指令
2. 判断是否以 "@" 开头 → 取第 2 个词为指令
3. 去除前导 "/" 得到 cmd_text
```

| 场景 | `actual_cmd` | `cmd_text` |
| :--- | :--- | :--- |
| `/顺时针` | `/顺时针` | `顺时针` |
| `顺时针` | `顺时针` | `顺时针` |
| `@某人 顺时针` | `顺时针` | `顺时针` |
| `/调速 2.0` | `/调速 2.0` | `调速 2.0` |

### 3.3 匹配模式守卫

```python
if not self._match_mode and not actual_cmd.startswith("/"):
    return  # 不处理
```

- `反色` 指令**不设守卫**，直接匹配即可触发
- 其他所有指令**默认需要 `/` 前缀**，除非 `match_mode = true`

### 3.4 指令分发

每条指令遵循相同的处理模式：

```
匹配 cmd_text → 检查守卫 → 获取图片 URL → event.stop_event() → 处理 → yield
```

其中 `event.stop_event()` 阻止 AstrBot 将该消息继续传递给后续插件。

---

## 4. 图像处理管道

### 4.1 单图处理（`_download_and_process`）

```
┌──────────┐     ┌──────────────┐     ┌───────────┐     ┌──────────┐
│ 图片 URL │ ──▶ │  下载到 tmp   │ ──▶ │ processor │ ──▶ │  Comp.Image │
└──────────┘     └──────────────┘     └───────────┘     └──────────┘
                       │                     │                │
                       ▼                     ▼                ▼
                 _download_sync      meme/*.py 函数    10s 后清理
```

所有单图 processor 签名为：

```python
def processor(input_path: str, output_path: str) -> str:
    # 返回 output_path
```

### 4.2 双图处理（`_dual_avatar`）

用于 `操你` / `抽你` 两个双人互动表情包：

```
发送者 QQ ──▶ 下载头像 ──┐
                        ├──▶ do.generate_do / lash.generate_lash ──▶ Comp.Image
被 @ 者 QQ ──▶ 下载头像 ──┘
```

> 两个头像均通过 QQ 头像 API 获取，签名不同：`(c_path, t_path, o_path)`。

### 4.3 各处理模块函数签名

| 模块 | 函数 | 签名 |
| :--- | :--- | :--- |
| `invert` | `invert_image` | `(input, output) -> str` |
| `flip` | `flip_horizontal` / `flip_vertical` | `(input, output) -> str` |
| `rotate` | `rotate_clockwise` / `rotate_counterclockwise` | `(input, output) -> str` |
| `mirror` | `mirror_left/right/top/bottom` | `(input, output) -> str` |
| `gif_speed` | `adjust_gif_speed` | `(input, output, speed) -> (str, float, str|None)` |
| `petpet` | `generate_petpet` | `(input, output) -> str` |
| `shoot` | `generate_shoot` | `(input, output) -> str` |
| `do` | `generate_do` | `(char1_path, char2_path, output) -> None` |
| `lash` | `generate_lash` | `(char1_path, char2_path, output) -> None` |
| `behead` | `generate_behead` | `(input, output) -> str` |

---

## 5. GIF 帧展开与保存

### 5.1 核心问题

GIF 的大部分帧只存储**相对于前一帧的增量区域**（非全帧），直接逐帧操作会产生黑块或残留。因此插件使用统一的 `unfold_frames()` 将所有帧复合为完整 RGBA 帧后再处理。

### 5.2 管道

```python
# gif_utils.py
def unfold_frames(gif) -> (list[Image], list[int]):
    for frame in ImageSequence.Iterator(gif):
        # 根据 disposal 决定画布恢复策略:
        #   disposal=2 → 恢复背景色
        #   disposal=3 → 恢复到两帧前的状态
        #   其他 → 继续累积
        composited.alpha_composite(frame_rgba)
        frames.append(composited.copy())
        durations.append(frame.info.get("duration"))
```

### 5.3 保存（`save_rgba_gif`）

手工写入 GIF 二进制流（非 Pillow 内置 `save`），确保：

1. **统一全局调色板**：所有帧共享同一个 256 色调色板
2. **统一透明索引**：所有帧使用同一个 transparent index
3. **复用原调色板**：翻转/旋转/对称等不改变颜色的操作，传入 `source_palette` 复用原 GIF 调色板，避免重新量化导致颜色偏移
4. **自动量化**：反色等改变颜色的操作，从第一帧自动量化生成新调色板

```
写入流程:
  GIF89a Header
  → Logical Screen Descriptor
  → Global Color Table (256 colors)
  → Netscape Extension (loop)
  → For each frame:
      Graphic Control Extension (disposal, duration, transparency)
      → Image Descriptor
      → LZW compressed pixel data
  → Trailer (0x3B)
```

### 5.4 调色板策略对比

| 操作类型 | 调色板来源 | 颜色保真度 | 示例 |
| :--- | :--- | :--- | :--- |
| 翻转/旋转/对称 | 复用原 GIF 调色板 | 完美无损 | 左右翻转, 顺时针, 左对称 |
| 反色 | 从第一帧自动量化 | 稍有偏差 | 反色 |
| 调速 | 复用原 GIF 调色板 | 完美无损 | 调速 2.0 |

---

## 6. 表情包生成器

### 6.1 摸头杀 (`petpet.py`)

- 输入：单张头像
- 算法：基于 B1gM8c/Petpet，手部逐帧覆盖头像并产生平滑变形
- 资源：`resource/petpet_hand.png`（精灵图，5 帧 × 手掌位置）

### 6.2 发射 (`shoot.py`)

- 输入：单张头像
- 依赖：`opencv-python` + `lbpcascade_animeface.xml`
- 算法：人脸检测定位后，将头像嵌入 13 帧射击底图序列
- 资源：`resource/shoot_frames/`

### 6.3 撅人 (`do.py`) 和 鞭笞 (`lash.py`)

- 输入：两个头像（发送者 + 被 @ 者）
- 算法：将两个头像分别嵌入多帧底图
- 资源：`resource/do_frames/`（3 帧）、`resource/lash_frames/`（9 帧）

### 6.4 砍头 (`behead.py`)

- 输入：单张头像
- 触发条件：必须 `@用户`（无引用或直接图片回退）
- 资源：`resource/behead_frames/`（21 帧）

---

## 7. 图片来源优先级

```python
def _extract_image_url(event) -> str | None:
    # 1. 引用回复中的图片
    for comp in event.get_messages():
        if isinstance(comp, Comp.Reply):
            for rc in comp.chain:
                if isinstance(rc, Comp.Image):
                    return rc.url

    # 2. 直接发送的图片
    for comp in event.get_messages():
        if isinstance(comp, Comp.Image):
            return comp.url

    return None
```

在全指令处理中，优先级为：

1. `@用户` 头像（若 `enable_at_avatar = true` 且消息包含 @）
2. 引用回复中的图片
3. 直接发送的图片
4. 无可用图片 → 静默返回

---

## 8. 文件生命周期与清理

### 8.1 临时文件命名

```
{tmp_dir}/pt_in_{pid}_{uuid8}.tmp   → 输入文件
{tmp_dir}/pt_out_{pid}_{uuid8}.gif  → 输出文件（或 .png）
```

使用 PID + UUID 避免并发生成时的冲突。双头像模式使用 `pt_da_*` 前缀。

### 8.2 清理时序

```
处理完成
  │
  ├─ 立即删除输入文件 (finally 块)
  │
  └─ 返回 Comp.Image 给 AstrBot
       │
       └─ asyncio.ensure_future:
            sleep(10s) → 删除输出文件
                            (给 QQ 足够时间上传)
```

### 8.3 崩溃残留清理

```python
def _cleanup_stale_tempfiles():
    # 启动时扫描 tmp_dir
    # 删除 mtime > 1 小时的 pt_* 文件
```

---

## 9. 配置系统

### 9.1 配置 Schema

通过 `_conf_schema.json` 定义，AstrBot 管理面板自动渲染配置表单。

```json
{
  "enable_at_avatar": {
    "type": "boolean",
    "default": true,
    "description": "允许 @用户取头像"
  },
  "match_mode": {
    "type": "boolean",
    "default": false,
    "description": "精准匹配模式"
  },
  "gif_speed_allow_frame_drop": {
    "type": "boolean",
    "default": false,
    "description": "调速允许丢帧"
  }
}
```

### 9.2 配置影响范围

| 配置 | 影响范围 |
| :--- | :--- |
| `enable_at_avatar` | 所有支持 @取头像的指令（全部变换类 + 表情包类） |
| `match_mode` | 除 `反色` 外所有指令的触发方式 |
| `gif_speed_allow_frame_drop` | 仅 `调速` 指令的倍率策略 |

---

<p align="center"><em>最后更新: 2026-06-26</em></p>
