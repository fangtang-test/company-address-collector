# -*- coding: utf-8 -*-
"""企某查地址查询结果采集：批量搜索、分页提取和 CSV 导出。"""
import csv
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
import argparse
import os

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / "browser_profile"  # 独立登录配置，不使用系统浏览器配置
ADDRESS_FILE = HERE / "addresses.txt"
DEFAULT_STATUS = "20,10,50"                 # 登记状态筛选；地区默认全国不限
CONFIG_FILE = HERE / "site_config.json"
HOME_URL = ""  # 运行时由本地配置或命令行指定

EXTRACT_JS = r"""
() => {
  const STATUS_RE = /(存续|在业|开业|在营|迁入|正常|注销|吊销|迁出|清算|停业|撤销)/;
  const out = [];
  const seen = new Set();
  const links = Array.from(document.querySelectorAll('a[href*="/firm/"]'));
  for (const a of links) {
    const href = a.getAttribute('href') || '';
    if (!/\/firm\/[0-9a-zA-Z]+\.html/.test(href)) continue;
    const raw = (a.innerText || '').replace(/\s+/g, '').trim();
    if (!raw) continue;
    // 从链接向上爬，找包含"地址"的最小卡片容器
    let card = a.parentElement, cardEl = null;
    for (let i = 0; i < 12 && card; i++) {
      if ((card.innerText || '').includes('地址')) { cardEl = card; break; }
      if (i > 0 && card.querySelectorAll('a[href*="/firm/"]').length > 1) break;
      card = card.parentElement;
    }
    if (!cardEl || seen.has(cardEl)) continue;
    // 只认卡片里第一个带文字的公司链接，避免把股东/对外投资链接当主公司
    const firstNamed = Array.from(cardEl.querySelectorAll('a[href*="/firm/"]'))
      .find(x => (x.innerText || '').replace(/\s+/g, '').trim().length >= 4);
    if (firstNamed !== a) continue;
    seen.add(cardEl);
    const name = raw.replace(/(存续|在业|开业|在营|迁入|正常|注销|吊销|迁出|清算|停业|撤销)$/, '').trim();
    if (name.length < 4 || name.length > 80) continue;
    const text = cardEl.innerText || '';
    const am = text.match(/(?:注册地址|地址)\s*[:：]\s*([^\n]+)/);
    const near = (a.parentElement && a.parentElement.innerText) || '';
    const sm = near.match(STATUS_RE) || text.match(STATUS_RE);
    out.push({ name: name, status: sm ? sm[1] : '', addr: am ? am[1].trim().replace(/复制$/, '') : '' });
  }
  return out;
}
"""


def log(msg):
    print(msg, flush=True)


def human_pause(a=2.0, b=4.5):
    time.sleep(random.uniform(a, b))


def build_search_url(key, region="", status=DEFAULT_STATUS):
    """region 为空 = 全国不限地区；省份码或 '省份码:区县码'；编码由目标站点定义"""
    flt = {}
    if region and region.strip():
        pr, _, cc = region.partition(":")
        chain = {"pr": pr.strip()}
        codes = [int(c) for c in cc.split(",") if c.strip().isdigit()]
        if codes:
            chain["cc"] = codes
        flt["rchain"] = [chain]
    if status and status.strip():
        flt["s"] = [s.strip() for s in status.split(",") if s.strip()]
    url = HOME_URL.rstrip("/") + "/web/search?key=" + quote(key.strip())
    if flt:
        url += "&filter=" + quote(json.dumps(flt, separators=(",", ":"), ensure_ascii=False), safe="")
    return url


def load_keys(file_path, single_key=None):
    """返回 [(地址, 地区限定)]。清单里可用「地址 | P1:100001」给单行加地区限定，| 后留空则全国"""
    if single_key:
        return [(single_key, "")]
    p = Path(file_path)
    if not p.exists():
        p.write_text(
            "# 一行一个地址；要限定省份/区可在行尾加「 | P1:100001」（不限则不用加）\n"
            "示例路100号\n", encoding="utf-8")
        log(f"未找到地址清单，已生成示例文件：{p}，请往里填地址后重跑。")
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        key, sep, region = ln.replace("｜", "|").partition("|")  # 兼容全角竖线
        out.append((key.strip(), region.strip() if sep else ""))
    return out


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def waf_blocked(page):
    """企某查 WAF：405 拦截页"""
    return "405.html" in (page.url or "") or "您的访问被阻断" in body_text(page)


def looks_like_captcha(page):
    t = body_text(page)
    return any(k in t for k in ("安全验证", "滑动验证", "请完成验证", "拖动滑块"))


def find_next_button(page):
    for sel in ('a:has-text("下一页")', 'button:has-text("下一页")',
                'li:has-text("下一页")', '[title="下一页"]'):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                return btn
        except Exception:
            pass
    return None


def bump_url_page(url):
    if re.search(r"[?&]p=\d+", url):
        return re.sub(r"([?&]p=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1), url)
    sep = "&" if "?" in url else "?"
    return url + sep + "p=2"


def scrape_key(page, key, max_pages, only_status, debug, auto=False, on_rows=None):
    """抓单个关键词的分页。on_rows(list) 每页新行回调，用于边抓边存。"""
    rows, seen_names, page_no, prev_names = [], set(), 1, None
    while True:
        if max_pages and page_no > max_pages:
            break
        log(f"    · 第 {page_no} 页")
        try:
            page.wait_for_selector('a[href*="/firm/"]', timeout=15000)
        except PWTimeout:
            if waf_blocked(page):
                log("    ! 被 WAF 拦截（405）。请检查账号权限或稍后再试")
                return rows
            if looks_like_captcha(page):
                if auto:
                    log("    ! 触发安全验证且为 --auto 模式，停止该关键词")
                    return rows
                log("    ! 触发安全验证，请在浏览器里完成后按回车")
                input("    >>> ")
                continue
            if debug:
                (HERE / "debug_card.html").write_text(page.content(), encoding="utf-8")
                log("    ! 没抓到条目，页面已导出到 debug_card.html")
            break

        page_rows = page.evaluate(EXTRACT_JS)
        new_this_page = []
        for r in page_rows:
            if not r["name"] or r["name"] in seen_names:
                continue
            if not r["status"]:
                continue  # 状态标签没识别出来的（如特殊主体类型）不要，保证口径干净
            if only_status and r["status"] not in only_status:
                continue  # --only 指定时才做二次过滤；默认信任 URL 状态筛选
            seen_names.add(r["name"])
            new_this_page.append((r["name"], r["status"], r["addr"]))
        if new_this_page:
            rows.extend(new_this_page)
            if on_rows:
                on_rows(new_this_page)

        # 免费账号翻页受限时，点击"下一页"后内容不变化（仍是原页面），
        # 若本页公司名单与上一页完全一致说明没翻过去，直接停，否则死循环
        cur_names = {r["name"] for r in page_rows}
        if prev_names is not None and cur_names == prev_names:
            log("    · 页面内容未变化（非会员翻页受限或已到末页），停止")
            break
        prev_names = cur_names

        if not page_rows:
            break

        btn = find_next_button(page)
        clicked = False
        if btn:
            try:
                cls = (btn.get_attribute("class") or "") + str(btn.get_attribute("aria-disabled") or "")
                if "disabled" in cls.lower():
                    break
                btn.scroll_into_view_if_needed()
                btn.click()
                clicked = True
            except Exception:
                pass
        if not clicked:
            nxt = bump_url_page(page.url)
            if nxt == page.url:
                break
            try:
                page.goto(nxt, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"    ! 翻页失败：{e}")
                break
        human_pause(3.0, 5.5)
        page_no += 1
        if waf_blocked(page):
            log("    ! 翻页后被 WAF 拦截，停止该关键词（等一会儿再跑或降速）")
            return rows
        if looks_like_captcha(page):
            if auto:
                log("    ! 触发安全验证且为 --auto 模式，停止该关键词")
                return rows
            log("    ! 触发安全验证，请在浏览器里完成后按回车")
            input("    >>> ")
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(description="企某查地址关键词批量查询结果采集")
    parser.add_argument("--base-url", default=os.environ.get("COLLECTOR_BASE_URL", ""), help="目标站点首页；也可在 site_config.json 配置")
    parser.add_argument("--file", default=str(ADDRESS_FILE), help="UTF-8 地址清单，一行一个")
    parser.add_argument("--key", help="只查询单个地址关键词")
    parser.add_argument("--region", default="", help="全局地区筛选，单行地区设置优先")
    parser.add_argument("--status", default=DEFAULT_STATUS, help="站点登记状态码；空值取消 URL 状态筛选")
    parser.add_argument("--max-pages", type=int, default=0, help="每个地址的最大页数；0 表示不设置页数上限")
    parser.add_argument("--out", help="CSV 输出路径")
    parser.add_argument("--only", default="", help="按卡片登记状态二次过滤，以逗号分隔")
    parser.add_argument("--proxy", help="显式指定代理；默认直连")
    parser.add_argument("--profile", help="独立浏览器配置目录，不要指向系统浏览器配置")
    parser.add_argument("--headless", action="store_true", help="无界面模式")
    parser.add_argument("--auto", action="store_true", help="跳过回车确认；遇到安全验证时停止当前关键词")
    parser.add_argument("--debug", action="store_true", help="无结果时保存页面 HTML 用于本地排查")
    args = vars(parser.parse_args(argv))
    if args["max_pages"] < 0:
        parser.error("--max-pages 不能小于 0")
    return args


def configure_site(base_url):
    """校验目标首页；发布版不提供真实站点域名。"""
    if not base_url and CONFIG_FILE.exists():
        try:
            base_url = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")).get("base_url", "")
        except (ValueError, AttributeError):
            raise ValueError("site_config.json 格式无效") from None
    if not isinstance(base_url, str):
        raise ValueError("base_url 必须为字符串")
    parts = urlsplit(base_url.strip())
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.hostname.endswith(".invalid")):
        raise ValueError("请在 site_config.json 或 --base-url 中填写目标站点首页，不要包含账号、密码或查询参数")
    return base_url.strip().rstrip("/") + "/"


def pick_profile(profile_arg=None):
    """返回独立的浏览器配置目录。"""
    return str((HERE / profile_arg).resolve()) if profile_arg else str(PROFILE_DIR)


def open_browser(p, proxy=None, headless=False, profile_arg=None):
    user_dir = pick_profile(profile_arg)
    args = ["--disable-blink-features=AutomationControlled", "--no-first-run"]
    args.append("--proxy-server=" + proxy if proxy else "--no-proxy-server")
    if not headless:
        args.append("--start-maximized")
    channels = ("chrome", "msedge")
    for channel in channels:
        try:
            return p.chromium.launch_persistent_context(
                user_dir, channel=channel, headless=headless,
                viewport=None, args=args, ignore_default_args=["--enable-automation"])
        except Exception:
            continue
    return p.chromium.launch_persistent_context(
        user_dir, headless=headless, viewport=None, args=args,
        ignore_default_args=["--enable-automation"])


def main():
    global HOME_URL
    a = parse_args(sys.argv[1:])
    try:
        HOME_URL = configure_site(a["base_url"])
    except ValueError as exc:
        log(str(exc))
        return 1
    keys = load_keys(a["file"], a["key"])
    if not keys:
        return
    only_status = [s for s in a["only"].split(",") if s] if a["only"] else []
    log(f"待查地址 {len(keys)} 个：{', '.join(k for k, _ in keys[:5])}{' ...' if len(keys) > 5 else ''}")
    if not any(r for _, r in keys) and not a["region"]:
        log("地区：全国不限（如某地址名太通用出现外省结果，可在清单该行尾加「 | 省码:区码」限定）")

    with sync_playwright() as p:
        ctx = open_browser(p, a["proxy"], a["headless"], a["profile"])
        try:
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome=window.chrome||{runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh']});")
        except Exception:
            pass
        page = ctx.pages[-1] if ctx.pages else ctx.new_page()
        log("使用独立浏览器配置 browser_profile，首次运行请在浏览器中登录")

        first_url = build_search_url(keys[0][0], keys[0][1] or a["region"], a["status"])
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)  # 先访问首页，带 referer
            human_pause(1.5, 3)
            page.goto(first_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"\n打开失败：{str(e).splitlines()[0]}")
            log("排查：① 梯子是否开着 ② 公司网络需代理请加 --proxy=... ③ 换个网络再试")
            ctx.close()
            return

        if waf_blocked(page):
            log("\n! 被企某查 WAF 拦截（405）。建议：")
            log("  1) 在独立浏览器中登录后检查账号权限")
            log("  2) 稍后再试")
            if not a["auto"]:
                input(">>> 或直接按回车继续尝试... ")

        if not a["auto"]:
            log("\n=============== 开工前确认 ===============")
            log("1. 浏览器里若未登录，先扫码登录企某查（登录态会记住）")
            log("2. 确认搜索结果正常显示后，回控制台按回车开始批量抓取")
            log("==========================================\n")
            input(">>> 按回车开始... ")
            if "/login" in page.url:
                page.goto(first_url, wait_until="domcontentloaded", timeout=60000)
                human_pause(2, 4)

        out = Path(a["out"]) if a["out"] else HERE / f"collector_结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        log(f"结果实时写入：{out}\n")
        total, summary, interrupted = 0, [], False
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["查询地址", "公司名称", "登记状态", "地址"])
            f.flush()
            try:
                for i, (key, key_region) in enumerate(keys, 1):
                    region = key_region or a["region"]
                    url = build_search_url(key, region, a["status"])
                    log(f"[{i}/{len(keys)}] {key}" + (f"（限 {region}）" if region else "（全国）"))
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        human_pause(2, 4)
                    except Exception as e:
                        log(f"    ! 打开失败，跳过：{str(e).splitlines()[0]}")
                        summary.append((key, 0))
                        continue
                    if waf_blocked(page):
                        log("    ! 被 WAF 拦截，跳过该地址（建议用已登录配置跑，或降速/稍后再试）")
                        summary.append((key, 0))
                        continue
                    key_rows = [0]
                    def on_rows(batch, _k=key, _kr=key_rows):
                        for r in batch:
                            w.writerow((_k,) + r)
                            _kr[0] += 1
                        f.flush()  # 边抓边落盘，Ctrl+C 不丢数据
                    rows = scrape_key(page, key, a["max_pages"], only_status, a["debug"], a["auto"], on_rows=on_rows)
                    total += key_rows[0]
                    summary.append((key, key_rows[0]))
                    log(f"    ✓ 抓到 {key_rows[0]} 家（累计 {total} 条）")
                    human_pause()
            except KeyboardInterrupt:
                interrupted = True
                log("\n⚠ 手动中断（Ctrl+C），已抓到的数据已全部写入文件。")
        log(f"\n✅ 共 {total} 条，已保存：{out}")
        log("—— 各地址命中数 ——")
        for k, n in summary:
            log(f"  {k}: {n} 家")

        if not a["auto"] and not interrupted:
            input(">>> 按回车关闭浏览器退出... ")
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
