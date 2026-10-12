# 浦东四街镇高德新店监控（云端版）

每天 07:00（北京时间）由 GitHub Actions 自动抓取高德 POI，严格按逆地理 `township` 核验高东镇、高行镇、高桥镇、浦兴路街道，更新原飞书云表格并发布 GitHub Pages 网站。每天 07:30 自动复核；发现未更新或数据不一致时自动补跑。

## 核心口径

- 类目：综合门店 `070000`、餐饮门店 `050000`、零售门店 `060000`
- 只有 `district=浦东新区` 且 `township` 精确命中四个目标街镇才能入榜
- 首次快照为存量基线，不计新店
- 新店第 1–15 天保留，第 16 天退出活跃清单，历史仍保留
- 电话仅使用高德真实返回值

## GitHub Secrets

仓库 Settings → Secrets and variables → Actions 中需配置：

- `AMAP_WEB_KEY`
- `LARKSUITE_CLI_APP_ID`
- `LARKSUITE_CLI_APP_SECRET`
- `STATE_ENCRYPTION_KEY`（随机长密码，仅用于加密数据库）

## 数据安全

SQLite 历史数据库不会以明文进入公开仓库。`state/poi_monitor.db.enc` 使用 AES-256-CBC/PBKDF2 加密，只有持有 `STATE_ENCRYPTION_KEY` 的 GitHub Actions 才能解密。

## 自动任务

- `.github/workflows/daily-update.yml`：每日 07:00 全量更新、验证、发布
- `.github/workflows/selfcheck.yml`：每日 07:30 校验，失败自动补跑

飞书表格地址保持不变：https://feishu.doubao.com/sheets/BEdas6XmMh4EzBtdvFVcJg4qnBd
