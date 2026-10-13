
import json
import re
import urllib.request
from html.parser import HTMLParser

URL = "https://shop.weverse.io/zh-cn/shop/JPY/artists/7/sales/42410"


class NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.data = ""

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.data += data


def find_product(obj):
    if isinstance(obj, dict):
        if (
            obj.get("saleId") == 42410
            and isinstance(obj.get("option"), dict)
            and len(obj["option"].get("options", [])) == 13
        ):
            return obj

        for value in obj.values():
            result = find_product(value)
            if result:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = find_product(item)
            if result:
                return result

    return None


def main():
    print("Testing Weverse stock access...")

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
            "Cache-Control": "no-cache"
        }
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8")
        print("HTTP Status:", response.status)
        print("HTML length:", len(html))

    parser = NextDataParser()
    parser.feed(html)

    if not parser.data:
        raise RuntimeError("NEXT_DATA not found")

    data = json.loads(parser.data)
    product = find_product(data)

    if not product:
        raise RuntimeError("Product data not found")

    print("Product status:", product.get("status"))
    print("-" * 45)

    available = []

    for item in product["option"]["options"]:
        name = item.get("saleOptionName")
        stock_id = item.get("saleStockId")
        sold_out = item.get("isSoldOut")

        print(f"{name}: ID={stock_id}, soldOut={sold_out}")

        if product.get("status") != "SOLD_OUT" and sold_out is False:
            available.append(name)

    print("-" * 45)
    print("Potentially available:", available)
    print("Test completed successfully")


if __name__ == "__main__":
    main()
