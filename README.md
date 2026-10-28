# 影视仓配置聚合仓库（TVBox Config Aggregator）

![配置状态](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fxycvip%2Ftvbox-ysc-config%2Fmain%2Foutput%2Fshield.json)
![更新频率](https://img.shields.io/badge/%E6%9B%B4%E6%96%B0-%E6%AF%8F%E5%A4%A9%202%20%E6%AC%A1-blue)

聚合 **肥猫、饭太硬、王二小** 等多个影视仓 / TVBox 单仓配置，通过 **GitHub Actions 定时自动更新**，并把结果直接提交回本仓库，供影视仓、TVBox、OK影视等软件随时拉取最新接口。

> 所有配置均来自互联网公开分享，版权归原作者所有；本仓库仅做聚合与镜像，仅供学习交流，请勿商用。

---

## 一、源可用性状态

<!-- STATUS_START -->
> 🔄 最近更新：2026-10-28 17:20:26（北京时间） · 成功 **9/9** 个源

| 配置源 | 更新状态 |
| --- | --- |
| 肥猫 | ✅ 更新成功 |
| 饭太硬 | ✅ 更新成功 |
| 王二小 | ✅ 更新成功 |
| 摸鱼儿 | ✅ 更新成功 |
| 嗷呜 | ✅ 更新成功 |
| 老刘备 | ✅ 更新成功 |
| 小马 | ✅ 更新成功 |
| 4K小盒子 | ✅ 更新成功 |
| 小盒子单仓 | ✅ 更新成功 |

### 源配置链接（每条地址单独检测，点击代码框右上角复制）

> 可用性按 GitHub Actions 运行环境的 HTTP 检测结果判断；不同地区、运营商或客户端的访问结果可能不同。

**肥猫**

**地址 1：✅ 可用（HTTP 200）**

```text
http://肥猫.net/tv
```

**地址 2：❌ 不可用（请求超时）**

```text
http://肥猫.net/
```

**饭太硬**

**地址 1：✅ 可用（HTTP 200）**

```text
http://www.饭太硬.net/tv
```

**地址 2：✅ 可用（HTTP 200）**

```text
http://www.饭太硬.cc/tv
```

**地址 3：✅ 可用（HTTP 200）**

```text
https://cdn09022024.gitlink.org.cn/api/v1/repos/xxooo/in/raw/in.bmp
```

**王二小**

**地址 1：✅ 可用（HTTP 200）**

```text
https://d.kstore.dev/download/9280/wex.json
```

**地址 2：✅ 可用（HTTP 200）**

```text
https://9280.kstore.vip/newwex.json
```

**地址 3：✅ 可用（HTTP 200）**

```text
http://new.王二小放牛娃.top
```

**摸鱼儿**

**地址 1：✅ 可用（HTTP 200）**

```text
http://我不是.摸鱼儿.top
```

**地址 2：✅ 可用（HTTP 200）**

```text
https://6800.kstore.vip/fish.json
```

**嗷呜**

**地址 1：✅ 可用（HTTP 200）**

```text
http://www.英格里希嗷呜.top/tv
```

**地址 2：✅ 可用（HTTP 200）**

```text
https://9763.kstore.vip/aowu.json
```

**地址 3：✅ 可用（HTTP 200）**

```text
http://itv666.cc/aowu/config.webp
```

**老刘备**

**配置地址：✅ 可用（HTTP 200）**

```text
https://raw.liucn.cc/box/m.json
```

**小马**

**配置地址：✅ 可用（HTTP 200）**

```text
https://szyyds.cn/tv/x.json
```

**4K小盒子**

**地址 1：✅ 可用（HTTP 200）**

```text
http://xhztv.top/4k.json
```

**地址 2：✅ 可用（HTTP 200）**

```text
http://xhztv.top/xhz
```

**小盒子单仓**

**配置地址：✅ 可用（HTTP 200）**

```text
http://xhztv.top/xhz
```

<!-- STATUS_END -->

## 二、仓库结构

```
tvbox-ysc-config/
├── .github/workflows/update.yml   # GitHub Actions 自动更新工作流
├── config/sources.json            # 配置源列表（主地址 + 备用地址），可自行增删改
├── scripts/update.py              # 核心更新脚本（下载→校验→合并→落盘→更新状态）
├── output/                        # 更新产物（由脚本自动生成并提交）
│   ├── 多仓订阅.json               # 多仓聚合订阅（推荐，App 内可切换各源）
│   ├── 单仓聚合.json               # 单仓聚合（所有源 sites 合并成一个，开箱即用）
│   ├── shield.json                # shields.io 状态徽章数据
│   ├── status.json                # 各源更新状态（机器可读）
│   ├── feimao.json / fantaiying.json / ...  # 各源独立配置
│   └── ...
├── requirements.txt
└── README.md
```

## 三、在影视仓 / TVBox 中使用

部署完成后，把下面任一地址填入 App 的「设置 → 配置地址」：

<!-- ARTIFACT_LINKS_START -->
以下地址由 GitHub Actions 自动更新，每个代码框右上角可复制。

> HTTP 状态为 GitHub Actions 运行环境中的检测结果，不代表所有手机、电视或网络均可访问。

### 多仓订阅

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/%E5%A4%9A%E4%BB%93%E8%AE%A2%E9%98%85.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/%E5%A4%9A%E4%BB%93%E8%AE%A2%E9%98%85.json
```

### 单仓聚合

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/%E5%8D%95%E4%BB%93%E8%81%9A%E5%90%88.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/%E5%8D%95%E4%BB%93%E8%81%9A%E5%90%88.json
```

### 线路 · 肥猫

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/feimao.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/feimao.json
```

### 线路 · 饭太硬

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/fantaiying.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/fantaiying.json
```

### 线路 · 王二小

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/wangerxiao.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/wangerxiao.json
```

### 线路 · 摸鱼儿

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/moyuer.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/moyuer.json
```

### 线路 · 嗷呜

**原始直链状态：✅ HTTP 200**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/aowu.json
```

**代理链接状态：✅ HTTP 200**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/aowu.json
```

### 线路 · 老刘备

**原始直链状态：❌ HTTP 404（地址不存在）**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/laoliubei.json
```

**代理链接状态：❌ HTTP 404（地址不存在）**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/laoliubei.json
```

### 线路 · 小马

**原始直链状态：❌ HTTP 404（地址不存在）**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/xiaoma.json
```

**代理链接状态：❌ HTTP 404（地址不存在）**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/xiaoma.json
```

### 线路 · 4K小盒子

**原始直链状态：❌ HTTP 404（地址不存在）**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/4k.json
```

**代理链接状态：❌ HTTP 404（地址不存在）**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/4k.json
```

### 线路 · 小盒子单仓

**原始直链状态：❌ HTTP 404（地址不存在）**

**原始直链**

```text
https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/xiaohezi.json
```

**代理链接状态：❌ HTTP 404（地址不存在）**

**代理链接**

```text
https://gh-proxy.org/https://raw.githubusercontent.com/xycvip/tvbox-ysc-config/main/output/xiaohezi.json
```
<!-- ARTIFACT_LINKS_END -->

> 多仓订阅与单仓文件里的地址由脚本根据 `GITHUB_REPOSITORY` 环境变量自动生成。若国内直连 raw.githubusercontent.com 慢，可套一层 `ghproxy` / `gh-proxy` 等加速前缀。

## 四、自动更新机制

- 推送后，GitHub 自动识别 `.github/workflows/update.yml`。
- 默认每天 **02:00 和 14:00（UTC，即北京时间 10:00 和 22:00）** 自动更新一次。
- 也可在 **Actions → Auto Update TVBox Configs → Run workflow** 手动触发。
- 更新结果自动提交回 `main` 分支，README 状态表与徽章同步刷新。
- 工作流使用内置 `GITHUB_TOKEN`，无需配置任何密钥。

## 五、自定义配置源

编辑 `config/sources.json` 即可增删配置源或更换地址：

```json
{
  "id": "mysource",
  "name": "我的源",
  "urls": [
    "https://example.com/tv",
    "https://backup.example.com/tv"
  ]
}
```

- `id`：输出文件名（建议英文小写）。
- `name`：在多仓订阅与状态表中展示的名字。
- `urls`：依次尝试的地址列表，第一个成功的会被使用；可按"主地址在前、备用在后"排列。
- 修改后推送到 `main` 分支即会自动触发一次更新。

## 六、本地手动更新（可选）

```bash
pip install -r requirements.txt
REPO=xycvip/tvbox-ysc-config python scripts/update.py
```

## 七、脚本特性

- **中文域名自动转 punycode**：`饭太硬.com` → `xn--sss604efuw.com`，避免 DNS 解析失败。
- **多地址回退**：每个源按顺序尝试主/备用地址，单个地址失败不影响其他源。
- **智能重试**：仅对超时、连接错误、空响应等瞬态问题退避重试；4xx/5xx 等确定性失败直接跳过。
- **兼容多仓订阅**：既支持单仓（含 sites 字段），也支持多仓订阅（顶层 urls 数组）格式。
- **反爬兼容**：使用简洁 UA（部分接口对完整浏览器 UA 下发挑战页），自动剔除 JSON 内嵌的 `//` 注释，兼容未转义控制字符。
- **聚合单仓**：把所有成功源的 `sites` 按 key 去重合并（重复 key 自动加源前缀），`lives/parses` 等列表合并去重。
- **状态可视化**：每次更新自动刷新 README 状态表与 shields.io 徽章。
- **部分成功可用**：只要有一个源成功就产出可用订阅，失败源记录在 `status.json` 与 README 状态表。

## 八、常见问题

- **某个源一直失败？** 大部分源做了地区/频率限制，GitHub Actions 与本地网络环境不同，可能某个环境能访问、另一个不能。脚本会按 `urls` 顺序自动尝试所有备用地址，只把成功的写入。
- **自动提交会不会死循环？** 不会。push 触发只监听 `config/`、`scripts/`、`requirements.txt` 的变更，自动提交只改 `output/` 与 README 状态区，不会再次触发。

## 九、免责声明

本项目仅用于学习、测试与个人使用；聚合的接口资源来自互联网公开分享，版权归原作者所有。若涉及侵权，请联系删除相应内容。
