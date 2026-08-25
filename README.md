# a-stock

A 股每日 14:45 量化监测与筛选系统。仅用于监测和研究，不连接券商，不自动下单。

## 当前进度：第一阶段已完成

已实现开发顺序第 1 阶段：

- 东方财富实时全 A 股快照连接
- 腾讯证券实时全市场快照连接
- AKShare `stock_zh_a_spot_em` 可选适配器
- 上海主板、深圳主板、创业板股票池识别
- 结合证券代码、数据源市场字段、数据源证券状态和名称识别股票身份
- ST、*ST、风险警示、退市整理、科创板、北交所等排除及具体原因记录
- 基础行情字段标准化、单位标注、缺失和异常校验
- 每次抓取保存合格股票池、排除明细和可审计元数据；两份 CSV 合并即为原始股票池

第一阶段不会执行市值、涨幅、量比或换手率硬筛。历史 K 线、分钟 K 线、技术指标、评分、SQLite 和回测也尚未实现，严格保留到对应阶段。

## 实际数据源与切换机制

`config.yaml` 默认使用 `auto`，按以下顺序尝试真实数据源：

1. `eastmoney`：东方财富全 A 实时行情，默认主源。
2. `tencent`：腾讯证券全 A 实时行情，独立备用源。
3. `akshare`：可选第二备用适配器，调用 `stock_zh_a_spot_em`；需要额外安装 AKShare。

每个数据源必须先通过完整性校验才会被接受：原始行情不少于 4,000 行、目标股票池不少于 3,000 行、代码唯一、代码与市场字段一致、三个目标板块都有数据、关键行情无缺失。任一条件失败会记录错误并切换到下一数据源；所有数据源失败时程序直接退出，不生成结果，也不会用缓存、随机数、前后填充或其他数值代替真实行情。

2026-08-25 的实际验收中，东方财富连接被远端断开，系统按顺序切换到腾讯源并成功取得 5,550 行真实行情；排除科创板、北交所和风险警示证券后得到 4,405 行目标股票池，关键字段缺失为 0。

## 安装

建议 Windows 安装 Python 3.10 或更高版本，然后在仓库目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

如需启用 AKShare 备用源：

```powershell
python -m pip install "akshare>=1.18"
```

## 运行第一阶段

```powershell
python main.py --config config.yaml
```

强制指定数据源：

```powershell
python main.py --source eastmoney
python main.py --source tencent
python main.py --source akshare
```

仅联网校验、不保存文件：

```powershell
python main.py --source auto --no-save
```

程序只使用数据源真实返回值和明确的单位换算。腾讯源的 `previous_close` 由同一快照中的 `current_price - change_amount` 计算；成交量由“手”同时换算为“股”；成交额由万元换算为元；总/流通市值由亿元换算为元。结果默认写入：

```text
data/snapshots/YYYY-MM-DD/HHMMSS/
  basic_quotes.csv
  excluded_stocks.csv
  metadata.json
```

字段单位：成交量同时保存“手”和“股”；成交额和总/流通市值均为人民币元；涨跌幅、振幅、换手率等均为百分数而非小数。

## 真实数据验收

```powershell
python scripts/verify_stage1.py --config config.yaml
python -m pytest -m real_data -q
```

两条命令都会访问实时数据源，不使用 mock 或模拟行情。非交易时段得到的是最近一次实时快照；停牌或缺失关键行情的证券会进入排除明细，不会被补造数值。

## 当前已知限制

- 本阶段只能抓取当前实时/最近收盘快照，不能用 `--date` 复原历史时点；历史日 K、分钟 K 和无未来函数回测属于后续阶段。
- 东方财富公开接口可能主动断开连接或限流；`auto` 会记录失败并切换腾讯，腾讯失败后才尝试 AKShare。
- 腾讯源当前不提供 `high`、`low`、`open`、`pb` 和 `change_5m_pct`，这些非第一阶段关键字段保持为空，不跨源拼接，也不填充。
- AKShare 适配器的市场字段由证券代码规则推断，并在输出的 `market_field_source` 中明确标记；它不是腾讯源的替代字段来源。
- `config.yaml` 已预留后续筛选参数，但第一阶段不会读取这些参数执行筛选。
- 公开行情接口可能调整字段或单位，因此程序采取失败关闭策略：字段、行数或单位校验不通过时不输出候选数据。
- 本项目只做研究和监测，不连接券商、不下单。
