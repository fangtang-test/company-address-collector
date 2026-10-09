# 企某查地址采集工具

使用 Python 和 Playwright，按地址关键词批量查询企业信息页面，提取公司名称、登记状态和地址，导出 CSV。

本仓库为脱敏展示版，站点名称统一写作“企某查”，真实域名由本地配置提供。示例地址和企业名称均为虚构数据。

## 功能

- 从文本清单读取地址，一行一个，支持单行地区筛选。
- 逐页读取搜索结果，按公司名称去重。
- 支持地区、登记状态、最大页数和输出路径参数。
- 每页结果实时写入 CSV，中断后保留已采集的数据。
- 使用独立浏览器目录保存登录状态，首次运行需要手动登录。

## 环境

- Python 3.10 或更新版本
- Playwright
- Chrome、Edge，或 Playwright 安装的 Chromium

## 安装

```bash
git clone https://github.com/fangtang-test/company-address-collector.git
cd company-address-collector
python -m venv .venv
```

Windows 激活虚拟环境：

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

安装依赖和浏览器：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 配置

复制 `site_config.example.json` 为 `site_config.json`，填写目标站点首页：

```json
{
  "base_url": "https://example.invalid/"
}
```

`example.invalid` 只是占位域名，运行前需要替换。也可以通过 `--base-url` 或环境变量 `COLLECTOR_BASE_URL` 指定首页。

脚本的搜索路径与页面选择器针对特定页面结构编写。更换域名不代表兼容其他站点，页面结构不同需要调整 `collector.py` 中的选择器和提取逻辑。

复制 `addresses.example.txt` 为 `addresses.txt`，替换为自己的地址清单：

```text
示例路100号
示例街20号
```

限定某一行的地区时，在地址后加 `| 省份码:区县码`。地区编码和登记状态码以目标站点实际筛选参数为准。清单单行的地区设置优先于全局 `--region`。

## 使用

```bash
python collector.py
```

Windows 也可以双击 `run_spider.bat`。脚本优先使用项目的 `.venv`，否则使用系统 Python。

首次运行，在打开的浏览器中登录并确认搜索结果正常显示，然后回终端按回车开始。遇到安全验证时需要手动完成；账号或页面权限限制不会被绕过。

查询单个地址：

```bash
python collector.py --key="示例路100号" --max-pages=5
```

指定清单和输出文件：

```bash
python collector.py --file="addresses.txt" --out="results.csv" --max-pages=10
```

取消 URL 中的状态筛选，或按页面登记状态二次过滤：

```bash
python collector.py --status=
python collector.py --only="存续,在业"
```

其他参数：

| 参数 | 说明 |
| --- | --- |
| `--region` | 全局地区筛选，默认不限制地区 |
| `--status` | 目标站点的状态码，默认 `20,10,50` |
| `--max-pages` | 每个地址的最大页数，默认 `0`，不设置页数上限 |
| `--profile` | 独立浏览器目录，默认 `browser_profile` |
| `--proxy` | 显式指定代理，默认直连 |
| `--headless` | 无界面模式，不适合首次登录或手动验证 |
| `--auto` | 跳过回车确认，遇到安全验证时停止当前关键词 |
| `--debug` | 未提取到条目时保存页面 HTML，便于本地排查 |

查看全部参数：

```bash
python collector.py --help
```

## 输出

默认生成 `collector_结果_时间戳.csv`，使用 UTF-8 BOM 编码，Excel 可直接打开。

| 查询地址 | 公司名称 | 登记状态 | 地址 |
| --- | --- | --- | --- |
| 示例路100号 | 示例企业甲（虚构） | 存续 | 示例市示例区示例路100号 |

完整格式见 `examples/results.example.csv`。

## 项目结构

```text
collector.py                 采集脚本
run_spider.bat               Windows 启动脚本
requirements.txt            Python 依赖
site_config.example.json    站点配置模板
addresses.example.txt       虚构地址示例
examples/                   虚构输出与页面样例
tests/                      离线测试
```

## 注意事项

- 查询结果是地址关键词关联的页面记录，不能直接认定所有企业的当前工商注册地址都与输入完全一致。
- 没有识别到登记状态标签的条目会被跳过；结果数量不代表站点全部记录数量。
- 登录、会员权限、验证限制或页面改版可能影响结果。搜索页未提取到条目不代表该地址没有企业。
- `site_config.json`、`addresses.txt`、浏览器登录目录、调试 HTML 和真实结果文件只保留在本机，不要提交到仓库。
- 请按目标站点的使用规则和账号权限操作，控制采集频率。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用虚构数据校验清单解析、搜索 URL、参数和页面提取，不访问真实站点，也不需要登录账号。
