import asyncio
from dataclasses import dataclass, field

import httpx

WB_SEARCH_URL = "https://u-search.wb.ru/exactmatch/ru/common/v18/search"
WB_FEEDBACKS_URL = "https://feedbacks2.wb.ru/feedbacks/v2/{imt_id}"
WB_PRODUCT_URL = "https://www.wildberries.ru/catalog/{article}/detail.aspx"

BASKETS = [
    (143, 1), (287, 2), (431, 3), (719, 4),
    (1007, 5), (1061, 6), (1115, 7), (1169, 8),
    (1313, 9), (1601, 10), (1655, 11), (1919, 12),
    (2045, 13), (2189, 14), (2405, 15), (2621, 16),
    (2837, 17), (3053, 18), (3269, 19), (3485, 20),
    (3701, 21), (3917, 22), (4133, 23), (4349, 24),
    (4565, 25), (4781, 26), (4997, 27), (5213, 28),
    (5429, 29), (5645, 30),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}


@dataclass
class Product:
    article: int
    name: str
    brand: str = ""
    price_rub: float = 0
    sale_price_rub: float = 0
    rating: float = 0
    feedbacks: int = 0
    url: str = ""
    photos: list[str] = field(default_factory=list)


def _basket_host(vol: int) -> str:
    for threshold, num in BASKETS:
        if vol <= threshold:
            return f"basket-{num:02d}.wbbasket.ru"
    # For vol beyond the table, extrapolate with step 216
    last_threshold, last_num = BASKETS[-1]
    extra = (vol - last_threshold - 1) // 216 + 1
    return f"basket-{last_num + extra:02d}.wbbasket.ru"


def _build_photo_urls(article: int, count: int = 2) -> list[str]:
    vol = article // 100_000
    part = article // 1_000
    host = _basket_host(vol)
    return [
        f"https://{host}/vol{vol}/part{part}/{article}/images/big/{i}.webp"
        for i in range(1, count + 1)
    ]


async def _fetch_via_search(client: httpx.AsyncClient, article: int) -> Product | None:
    """Primary: get all data from the search API."""
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "lang": "ru",
        "page": 1,
        "query": str(article),
        "resultset": "catalog",
        "sort": "popular",
        "spp": 30,
        "suppressSpellcheck": "false",
    }
    resp = await client.get(WB_SEARCH_URL, params=params)
    if resp.status_code == 429:
        return None
    resp.raise_for_status()
    data = resp.json()

    products = data.get("data", {}).get("products", data.get("products", []))
    target = None
    for p in products:
        if p.get("id") == article:
            target = p
            break
    if target is None and products:
        target = products[0]
    if target is None:
        return None

    sizes = target.get("sizes", [])
    price_basic = 0
    price_product = 0
    if sizes:
        price_info = sizes[0].get("price", {})
        price_basic = price_info.get("basic", 0)
        price_product = price_info.get("product", 0)

    return Product(
        article=article,
        name=target.get("name", "Без названия"),
        brand=target.get("brand", ""),
        price_rub=price_basic / 100,
        sale_price_rub=price_product / 100,
        rating=target.get("reviewRating", target.get("rating", 0)),
        feedbacks=target.get("feedbacks", target.get("nmFeedbacks", 0)),
        url=WB_PRODUCT_URL.format(article=article),
        photos=_build_photo_urls(article),
    )


def _calc_nm_rating(dist: dict) -> float:
    total = sum(dist.values())
    if total == 0:
        return 0.0
    weighted = sum(int(star) * count for star, count in dist.items())
    return round(weighted / total, 1)


def _cdn_base_url_with_basket(article: int, basket_num: int) -> str:
    vol = article // 100_000
    part = article // 1_000
    return f"https://basket-{basket_num:02d}.wbbasket.ru/vol{vol}/part{part}/{article}/info"


async def _fetch_via_cdn(client: httpx.AsyncClient, article: int) -> Product | None:
    """Fallback: combine CDN card.json + feedbacks + price-history."""
    vol = article // 100_000
    primary_basket = _basket_host(vol).replace("basket-", "").replace(".wbbasket.ru", "")
    primary_num = int(primary_basket)

    baskets_to_try = [primary_num]
    for d in range(1, 15):
        if primary_num - d >= 1:
            baskets_to_try.append(primary_num - d)
    for d in range(1, 6):
        if primary_num + d <= 45:
            baskets_to_try.append(primary_num + d)

    base = None
    card = None
    for bn in baskets_to_try:
        url = _cdn_base_url_with_basket(article, bn) + "/ru/card.json"
        resp = await client.get(url)
        if resp.status_code == 200:
            base = _cdn_base_url_with_basket(article, bn)
            card = resp.json()
            break
    if base is None or card is None:
        return None

    name = card.get("imt_name", "Без названия")
    imt_id = card.get("imt_id")

    brand = ""
    try:
        sr = await client.get(base + "/sellers.json")
        if sr.status_code == 200:
            brand = sr.json().get("trademark", "")
    except Exception:
        pass

    rating = 0.0
    feedbacks_count = 0
    if imt_id:
        try:
            fb_resp = await client.get(WB_FEEDBACKS_URL.format(imt_id=imt_id))
            if fb_resp.status_code == 200:
                fb_data = fb_resp.json()
                for nm_entry in fb_data.get("nmValuationDistribution", []):
                    if nm_entry.get("nm") == article:
                        dist = nm_entry.get("valuationDistribution", {})
                        feedbacks_count = sum(dist.values())
                        rating = _calc_nm_rating(dist)
                        break
                else:
                    val = fb_data.get("valuation", "0")
                    rating = float(val) if val else 0.0
                    feedbacks_count = fb_data.get("feedbackCount", 0)
        except Exception:
            pass

    sale_price = 0.0
    try:
        pr = await client.get(base + "/price-history.json")
        if pr.status_code == 200:
            history = pr.json()
            if history:
                last = history[-1]
                sale_price = last.get("price", {}).get("RUB", 0) / 100
    except Exception:
        pass

    return Product(
        article=article,
        name=name,
        brand=brand,
        price_rub=0,
        sale_price_rub=sale_price,
        rating=rating,
        feedbacks=feedbacks_count,
        url=WB_PRODUCT_URL.format(article=article),
        photos=_build_photo_urls(article),
    )


async def fetch_product(article: int, search_retries: int = 2) -> Product | None:
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers=HEADERS
    ) as client:
        for attempt in range(search_retries):
            product = await _fetch_via_search(client, article)
            if product is not None:
                return product
            if attempt < search_retries - 1:
                await asyncio.sleep(1.5)
        return await _fetch_via_cdn(client, article)
