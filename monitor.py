import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

SALE_ID = 42410
SHOP_URL = "https://shop.weverse.io/zh-cn/shop/JPY/artists/7/sales/42410"
ISSUE_TITLE = "[weverse-stock-monitor-state] sale-42410"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-monitor/1.0)", "Accept": "text/html,application/json", "Cache-Control": "no-cache", "Pragma": "no-cache"}

class NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.result = []
    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.inside = True
    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False
    def handle_data(self, data):
        if self.inside:
            self.result.append(data)

def request_json(url, method="GET", payload=None, token=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "weverse-stock-monitor", "X-GitHub-Api-Version": "2022-11-28"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API HTTP {e.code} ({method})") from None

def find_product(obj):
    if isinstance(obj, dict):
        if str(obj.get("saleId")) == str(SALE_ID) and isinstance(obj.get("option"), dict):
            opts = obj["option"].get("options")
            if isinstance(opts, list) and len(opts) == 13:
                return obj
        for v in obj.values():
            found = find_product(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_product(v)
            if found is not None:
                return found
    return None

def current_stock():
    separator = "&" if "?" in SHOP_URL else "?"
    url = f"{SHOP_URL}{separator}_monitor_time={int(time.time())}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    parser = NextDataParser()
    parser.feed(html)
    if not parser.result:
        raise RuntimeError("No __NEXT_DATA__ in response")
    product = find_product(json.loads("".join(parser.result)))
    if product is None:
        raise RuntimeError("Expected product and 13 options not found")
    status = product.get("status")
    if not isinstance(status, str) or not status:
        raise RuntimeError("Invalid product status")
    options = product["option"]["options"]
    result = {}
    for item in options:
        name, stock_id, sold = item.get("saleOptionName"), item.get("saleStockId"), item.get("isSoldOut")
        if not isinstance(name, str) or not name or not isinstance(stock_id, int) or type(sold) is not bool:
            raise RuntimeError("Unexpected SKU data")
        if name in result:
            raise RuntimeError("Duplicate option name")
        result[name] = {"stockId": stock_id, "isSoldOut": sold}
    if len(result) != 13:
        raise RuntimeError("Expected exactly 13 distinct SKUs")
    # A false isSoldOut alone is insufficient when the entire sale is SOLD_OUT.
    available = sorted(name for name, data in result.items() if status != "SOLD_OUT" and not data["isSoldOut"])
    return status, result, available

def get_state_issue(repo, token):
    issues = request_json(f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100", token=token)
    for issue in issues:
        if issue.get("title") == ISSUE_TITLE and "pull_request" not in issue:
            return issue
    return None

def send_telegram(names, status):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram secrets are empty")
    message = ("🔔 Weverse 疑似补货提醒！\n\n" + "\n".join(f"• {name}" for name in names)
               + f"\n\n商品整体状态：{status}\n请立即打开页面确认是否能购买：\n{SHOP_URL}"
               + "\n\n注：此为页面状态检测，不保证结账成功。")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Do not let urllib's exception display the credential-bearing URL.
    try:
        body = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as response:
            ok = json.load(response).get("ok")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram API HTTP {e.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Telegram network request failed") from None
    if not ok:
        raise RuntimeError("Telegram API did not confirm delivery")

def main():
    repo, gh_token = os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"].strip()
    status, options, available = current_stock()
    print(f"Sale {SALE_ID}: status={status}; SKUs={len(options)}; potential availability={available}")
    issue = get_state_issue(repo, gh_token)
    previous = None
    if issue:
        try:
            old = json.loads(issue.get("body") or "")
            if old.get("saleId") == SALE_ID and isinstance(old.get("available"), list):
                previous = set(old["available"])
            else:
                raise ValueError("Unexpected stored state")
        except (ValueError, TypeError):
            raise RuntimeError("Stored state issue invalid; manual review required") from None
    newly_available = sorted(set(available) - (previous if previous is not None else set()))
    if newly_available:
        print(f"Newly available: {newly_available}")
        send_telegram(newly_available, status)
    else:
        print("No new availability; no Telegram message")
    if previous is None or previous != set(available):
        body = json.dumps({"saleId": SALE_ID, "status": status, "available": available}, ensure_ascii=False, indent=2)
        endpoint = f"https://api.github.com/repos/{repo}/issues"
        if issue:
            request_json(f"{endpoint}/{issue['number']}", method="PATCH", payload={"body": body}, token=gh_token)
        else:
            request_json(endpoint, method="POST", payload={"title": ISSUE_TITLE, "body": body}, token=gh_token)
        print("Saved monitoring state in GitHub Issue")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Monitor failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
