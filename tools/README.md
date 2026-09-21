# tools · 辅助脚本

## backup_roundtrip.py — 备份往返验证

确认「换电脑迁移」不丢数据：起两个临时库（源 + 空目标），源库灌演示数据 → 导出 → 导入目标库 →
逐表核对条数与关键字段。

```bash
python tools/backup_roundtrip.py     # 退出码 0 = 全部一致
```

覆盖：资金曲线 / 期货交易记录 / 期权交易记录 / 监控池（两模式）/ 复盘笔记（两模式）/ 最近方案，
外加期货详情的开仓测算快照、初次止损止盈、平仓盈亏。**改了备份导出/导入相关代码后跑一次。**

> 注意：脚本会占用 8897 / 8896 两个端口，跑之前先关掉占用它们的服务。

## README 功能截图

主 README 里那几张界面图不是手工截的，用这三个脚本可以从**演示数据**一键重新生成。
演示数据全是编的，不碰任何真实交易记录。

## 为什么单独一套

- 截图必须来自**干净的临时库**（真实库里品种、金额都是私人的，不能进公开仓库）
- 界面截图要**高清且尺寸统一**，所以用 CDP 以 2 倍像素密度截，再裁边缩放

## 用法（在项目根目录依次执行）

```bash
# 1. 用临时数据目录起服务（⚠ 必须带 OC_DATA_DIR，否则会往真实库里写演示数据）
python -c "import tempfile;print(tempfile.mkdtemp(prefix='oc_shot_'))"   # 记下输出的目录
OC_NO_TAKEOVER=1 OC_PORT=8899 OC_DATA_DIR=<上面的目录> python main.py &

# 2. 起 headless Chrome（CDP 端口随便挑一个没被占的）
python _start_chrome.py 9231 &

# 3. 灌演示数据（交易记录 / 监控池 / 复盘 / 资金曲线）
python tools/readme_shots_seed.py

# 4. 截屏 → 裁切缩放（图片输出到 docs/screenshots/）
OC_E2E_BASE=http://127.0.0.1:8899 OC_CDP_PORT=9231 node tools/readme_shots.js
python tools/readme_shots_trim.py

# 5. 收工
curl "http://127.0.0.1:8899/api/shutdown"
```

## 三个脚本各干什么

| 脚本 | 作用 |
|---|---|
| `readme_shots_seed.py` | 通过真实 API 灌演示数据。期货首条的测算快照是**真的调 `/api/calc/futures` 算出来的**，所以详情页的测算卡不是假数据 |
| `readme_shots.js` | CDP 截屏。视口高度按内容自适应，2 倍像素密度；详情页 / 图表放大这类浮层单独框住 |
| `readme_shots_trim.py` | 按截屏时记录的边界裁切，再自动去掉右侧与底部空白，统一缩放到 1380px 宽 |

## 改动截图时的注意点

- 演示数据改完记得**先清空资金曲线**再灌（`/api/funds/clear-all` 只清 records，不动交易记录）；
  交易记录部分不能重复灌，会插出重复行
- 图表在窄栏里数据点超过 ~12 个月标签就会重叠 —— 演示数据控制在 12 个月以内
- 截图坐标用「滚动 + 整屏截图」，**不要用 `Page.captureScreenshot` 的 clip**
  （同一 Chrome 版本下 clip 的 y 坐标实测有偏移）
