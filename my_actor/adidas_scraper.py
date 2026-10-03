from playwright.async_api import TimeoutError as ClearcoteTimeoutError, BrowserContext, Page, Browser
from clearcote.async_api import launch_persistent_context, GeoipError
from invisible_playwright.async_api import InvisiblePlaywright
from playwright._impl._errors import TargetClosedError
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlencode
from camoufox.exceptions import UnknownIPLocation
from camoufox.async_api import AsyncCamoufox
from collections.abc import AsyncGenerator
from apify import ProxyConfiguration
from camoufox import DefaultAddons
from functools import wraps
from typing import Literal
from apify import Actor
import asyncio
import random
import time
import json
import os

RUNNING_LOCALLY: bool = True if os.environ.get("RUNNING_LOCALLY") == "yes" else False

REGIONS_DOMAINS = {
    "united-states": "adidas.com/us",
    "united-kingdom": "adidas.co.uk",
    "canada-en": "adidas.ca/en",
    "canada-fr": "adidas.ca/fr",
    "germany": "adidas.de",
    "france": "adidas.fr",
    "netherlands": "adidas.nl",
    "spain": "adidas.es",
    "italy": "adidas.it",
    "japan": "adidas.jp",
    "australia": "adidas.com.au",
    "new-zealand": "adidas.co.nz",
}

SPECIAL_REGIONS = [
    "united-states",
    "canada"
]

SPECIAL_REGIONS_SUFFIXES = {
    "united-states": "us",
    "canada-en": "en",
    "canada-fr": "fr"
}

SITEPATHS = {
    "united-states": "us",
    "united-kingdom": "uk",
    "canada-en": "ca",
    "canada-fr": "ca",
    "germany": "de",
    "france": "fr",
    "netherlands": "nl",
    "spain": "es",
    "italy": "it",
    "japan": "jp",
    "australia": "au",
    "new-zealand": "nz",
}

REVIEWS_LOCALES = {
    "united-states": "en*",
    "united-kingdom": "en*",
    "canada-en": "en*",
    "canada-fr": "fr*",
    "germany": "de*",
    "france": "fr*",
    "netherlands": "nl*",
    "spain": "es*",
    "italy": "it*",
    "japan": "ja*",
    "australia": "en*",
    "new-zealand": "en*"
}

BAZAARVOICE_LOCALES = {
    "united-states": "en_US",
    "united-kingdom": "en_GB",
    "canada": "en_CA",
    "germany": "de_DE",
    "france": "fr_FR",
    "netherlands": "nl_NL",
    "spain": "es_ES",
    "italy": "it_IT",
    "japan": "ja_JP",
    "australia": "en_AU",
    "new-zealand": "en_NZ"
}

def wait_for_cookie_refresh(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        scraper: AdidasScraper = args[0]
        while scraper.cookie_refresh_in_progress:
            await asyncio.sleep(0.07)

        return await func(*args, **kwargs)

    return wrapper

def auto_retry(max_attempts: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            scraper: AdidasScraper = args[0]
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)
                except AntiBotBlockError:
                    if attempt == max_attempts:
                        raise AntiBotBlockError(f"FUNCTION FAILED AFTER ALL ATTEMPTS: {func.__name__}")
                    
                    while scraper.cookie_refresh_in_progress:
                        await asyncio.sleep(0.7)
                    
                    await asyncio.sleep(min(2 ** attempt + random.uniform(0, 5), 45))
                    async with scraper.cookie_lock:
                        current_time = time.time()
                        if current_time - scraper.cookies_last_accessed_at > 10:
                            await scraper.refresh_cookies()
                    continue
                except TargetClosedError:
                    while scraper.cookie_refresh_in_progress:
                        await asyncio.sleep(0.07)
                    
                    continue
                else:
                    return result
        
        return wrapper
    
    return decorator

class AntiBotBlockError(Exception):
    """An error that is raised when the scraper is blocked by Akamai."""
    pass

class FatalAntiBotBlockError(Exception):
    """An error that is raised when the scraper is blocked by Akamai and cannot retrieve a new build ID."""
    pass

class InvalidUrlError(Exception):
    """An error that is raised when the scraper tries to scrape an invalid URL."""
    pass

class AdidasScraper:
    """A powerful, all-in-one Adidas store scraper that bypasses every bot detection test."""
    def __init__(self, options: dict, session_id: str, proxy_cfg: ProxyConfiguration = None, proxy_url: str = None) -> None:
        # Get hold of the proxy settings and initialize a curl_cffi session
        self.proxy_cfg = proxy_cfg
        self.proxy_url = proxy_url
        self.session_id = session_id  # Using sticky residential proxies
        self.used_session_ids = set([self.session_id])
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.http_client: Page | None = None
        self.cookies = {}
        self.cookies_last_accessed_at = 0.0
        self.cookie_refresh_in_progress = False
        self.cookie_lock = asyncio.Lock()
        self.proxy = {
            "server": "http://" + self.proxy_url.split("@")[-1],
            "username": self.proxy_url.split("//")[-1].split(":")[0],
            "password": self.proxy_url.split("//")[-1].split(":")[1].split("@")[0]
        } if self.proxy_url else None
        self.geoip: bool = True if self.proxy_url else False
        self.is_running = True
        self.page_interaction_task = None
        self.platform = "windows" if RUNNING_LOCALLY else "linux"

        self.adidas_store_build_id: str | None = None

        # Get hold of the user input
        self.mode = options.get("mode", "products") or "products"
        self.max_products = options.get("maxProducts")
        self.max_products_per_search = options.get("maxProductsPerSearch")
        self.max_reviews = options.get("maxReviews")
        self.max_reviews_per_product = options.get("maxReviewsPerProduct")
        self.reviews_sort = options.get("reviewsSort", "newest") or "newest"
        self.scrape_reviews_from_all_languages = options.get("scrapeReviewsFromAllLanguages", False) or False
        self.scrape_entire_website = False
        self.country = options.get("country", "united-states") or "united-states"
        self.search_urls = []
        self.product_urls = []
        
        # Scraping metrics
        self.scraped_products_count = 0
        self.scraped_reviews_count = 0
        self.seen_product_ids = set()
        self.seen_review_ids = set()
        self.seen_model_numbers = set()

        search_urls_raw = options.get("searchUrls", []) or []
        for item in search_urls_raw:
            self.search_urls.append(item["url"])

        product_urls_raw = options.get("productUrls", []) or []
        for item in product_urls_raw:
            self.product_urls.append(item["url"])

        # Concurrency-related settings
        self.items_queue = asyncio.Queue(maxsize=1000)
        self.http_request_semaphore = asyncio.Semaphore(value=50)
        self.product_detail_semaphore = asyncio.Semaphore(value=3)
        self.review_page_semaphore = asyncio.Semaphore(value=10)
        self.product_search_semaphore = asyncio.Semaphore(value=5)

        # An Apify Key-Value Store will be intialized if the user is on the products-monitoring mode.
        # self.kv_store: KeyValueStore | None = None

    async def initialize(self) -> None:
        # Get the initial session cookies and build ID.
        country_text = self.country.replace("-", " ").upper()
        cookie_url = f"https://www.{REGIONS_DOMAINS[self.country]}/search?q="

        Actor.log.info(f"ESTABLISHING CONNECTION TO ADIDAS {country_text}...")
        new_build_id_obtained = False
        for attempt in range(1, 6):
            try:
                # camoufox = AsyncCamoufox(
                #     os="windows" if RUNNING_LOCALLY else "linux",
                #     headless=True,
                #     humanize=True,
                #     fingerprint_preset=False,
                #     proxy=self.proxy if self.proxy_url else None,
                #     geoip=True if self.proxy_url else False,
                #     exclude_addons=[DefaultAddons.UBO]
                # )
                # self.browser = await camoufox.start()
                self.context: BrowserContext = await launch_persistent_context(
                    user_data_dir="./my_actor/clearcote/",
                    fingerprint=f"{self.session_id}",
                    platform=self.platform,
                    headless=False,
                    disable_gpu_fingerprint=True,
                    proxy=self.proxy,
                    geoip=self.geoip,
                    quiet=True,
                    humanize=True
                )
                self.http_client = await self.context.new_page()
                await self.http_client.goto(cookie_url)          
                await self.http_client.wait_for_selector('article[data-testid="plp-product-card"]', timeout=10000)
                
                try:
                    modal_btn = await self.http_client.wait_for_selector('#glass-gdpr-default-consent-accept-button', timeout=5000)
                    await asyncio.sleep(random.uniform(1, 2))
                    await self.http_client.evaluate(r'(el) => {el.click();}', modal_btn)
                except ClearcoteTimeoutError:
                    pass
                
                # await self.http_client.wait_for_timeout(2000)
                
                if not self.page_interaction_task:
                    self.page_interaction_task = asyncio.create_task(self.interact_naturally_with_page())
                
                page_html = await self.http_client.inner_html("html")
                self.adidas_store_build_id = page_html.split("/_buildManifest.js")[0].split("/")[-1]
            except (ValueError, ClearcoteTimeoutError):
                Actor.log.error(f"❌ CONNECTION ATTEMPT {attempt}/5 FAILED DUE TO A BAD PROXY. RETRYING...")
                if self.proxy_cfg:
                    while True:
                        self.session_id = f"{random.randint(0, 999999)}"
                        if self.session_id not in self.used_session_ids:
                            self.used_session_ids.add(self.session_id)
                            break
                    self.proxy_url = await self.proxy_cfg.new_url(session_id=f"{self.session_id}")
                
                await self.context.close()
                continue
            else:
                new_build_id_obtained = True
                break
            finally:
                self.cookies_last_accessed_at = time.time()

        if new_build_id_obtained:            
            Actor.log.info(f"ESTABLISHED CONNECTION TO ADIDAS {country_text} SUCCESSFULLY.")
            Actor.log.info("SCRAPING WILL BEGIN SHORTLY...")
        else:
            raise FatalAntiBotBlockError(f"COULD NOT ESTABLISH CONNECTION TO ADIDAS {country_text} AFTER 5 ATTEMPTS.")  # If the new build ID cannot be obtained, the scraper should crash here.

    async def close(self) -> None:
        await self.context.close()
    
    async def interact_naturally_with_page(self) -> None:
        while self.is_running:
            try:
                card_locator = self.http_client.locator('article[data-testid="plp-product-card"]')
                page_cards = self.http_client.query_selector_all('article[data-testid="plp-product-card"]')
                num_page_cards = len(page_cards)
                target_card = card_locator.nth(random.randint(0, num_page_cards - 1))
                await target_card.hover()
                await asyncio.sleep(random.uniform(0.88, 5))
            except TargetClosedError:
                while self.cookie_refresh_in_progress:
                    await asyncio.sleep(0.07)
                
                continue
            except ClearcoteTimeoutError:
                await asyncio.sleep(1.5)
                continue
    
    async def refresh_cookies(self) -> None:
        if self.cookie_refresh_in_progress:
            return
        
        self.cookie_refresh_in_progress = True
        cookie_refresh_successful = False
        cookie_url = f"https://www.{REGIONS_DOMAINS[self.country]}/search?q="

        Actor.log.info("REFRESHING COOKIES...")
        await self.context.close()
        for attempt in range(1, 6):
            try:
                self.context: BrowserContext = await launch_persistent_context(
                    user_data_dir="./my_actor/clearcote/",
                    fingerprint=f"{self.session_id}",
                    platform=self.platform,
                    headless=True,
                    disable_gpu_fingerprint=True,
                    proxy=self.proxy,
                    geoip=self.geoip,
                    quiet=True,
                    humanize=True
                )
                # camoufox = AsyncCamoufox(
                #     os="windows" if RUNNING_LOCALLY else "linux",
                #     headless=True,
                #     humanize=True,
                #     fingerprint_preset=False,
                #     proxy={
                #         "server": "http://" + self.proxy_url.split("@")[-1],
                #         "username": self.proxy_url.split("//")[-1].split(":")[0],
                #         "password": self.proxy_url.split("//")[-1].split(":")[1].split("@")[0]
                #     } if self.proxy_url else None,
                #     geoip=True if self.proxy_url else False,
                #     exclude_addons=[DefaultAddons.UBO]
                # )
                # self.browser = await camoufox.start()
                # self.context = await self.browser.new_context()
                self.http_client = await self.context.new_page()
                await self.http_client.goto(cookie_url)          
                await self.http_client.wait_for_selector('article[data-testid="plp-product-card"]', timeout=10000)
                # await self.http_client.wait_for_timeout(2000)
                
                try:
                    modal_btn = await self.http_client.wait_for_selector('#glass-gdpr-default-consent-accept-button', timeout=5000)
                    await asyncio.sleep(random.uniform(1, 2))
                    await self.http_client.evaluate(r'(el) => {el.click();}', modal_btn)
                except ClearcoteTimeoutError:
                    pass
                
                # if not self.page_interaction_task:
                #     self.page_interaction_task = asyncio.create_task(self.interact_naturally_with_page())
            except (ValueError, ClearcoteTimeoutError):
                Actor.log.error(f"❌ COOKIE REFRESH ATTEMPT {attempt}/5 FAILED DUE TO A BAD PROXY. RETRYING...")
                if self.proxy_cfg:
                    while True:
                        self.session_id = f"{random.randint(0, 999999)}"
                        if self.session_id not in self.used_session_ids:
                            self.used_session_ids.add(self.session_id)
                            break
                    self.proxy_url = await self.proxy_cfg.new_url(session_id=f"{self.session_id}")
                
                await self.browser.close()
                continue
            else:
                cookie_refresh_successful = True
                break
            finally:
                self.cookies_last_accessed_at = time.time()

        if cookie_refresh_successful:            
            self.cookie_refresh_in_progress = False
        else:
            raise FatalAntiBotBlockError("COULD NOT REFRESH COOKIES AFTER 5 ATTEMPTS.")

    def product_url_is_valid(self, url: str) -> bool:
        product_id = url.split(".html")[0].split("/")[-1]
        if len(product_id) > 6 or REGIONS_DOMAINS[self.country] not in url:
            return False
        else:
            return True
    
    def search_url_is_valid(self, url: str) -> bool:
        if REGIONS_DOMAINS[self.country] in url and len(url.split(f"{REGIONS_DOMAINS[self.country]}")[-1]) > 1:
            return True
        else:
            return False
    
    def parse_item(self, raw: dict) -> dict:
        data_type = raw["dataType"]
        genders = {
            "M": "men",
            "W": "women",
            "K": "kids",
            "U": "unisex"
        }
        
        # Parse Product
        if data_type == "product":
            product_id = raw["product_id"]
            product_name = raw["product_name"]
            product_url = raw["product_url"]
            product_model_number = raw["model_number"]
            product_description = (raw.get("description", {}) or {}).get("text")
            product_third_party_options = raw.get("third_party_eligible_options", []) or []
            product_rating = raw["ratingsData"]["overallRating"]
            product_review_count = raw["ratingsData"]["reviewCount"]
            product_general_customer_sentiments = [sentence.replace("* ", "") for sentence in raw["reviewsData"]["reviewSummaries"]["summary"].split("\n* ")] if raw["reviewsData"]["reviewSummaries"]["summary"] else []
            product_badges = [badge["text"] for badge in raw["badges"]]
            
            if "discount_price" in raw:
                product_price = raw.get("discount_price")
            else:
                product_price = raw.get("original_price")
            
            product_original_price = raw.get("original_price")
            product_discount_percentage = round(((product_original_price - product_price) / product_original_price) * 100, 2) if product_price and product_original_price else 0
            product_price_currency = raw.get("display_currency")
            product_availability = 0
            product_availability_status = "NOT_AVAILABLE"
            product_purchase_limit = raw.get("purchase_limit", 0) or 0
            product_color = raw.get("color")
            product_category = raw.get("division")
            product_category_slug = raw.get("plp_url")
            product_gender = genders.get(raw.get("gender") or "") or raw.get("gender")
            product_total_colors = raw.get("total_colors") or 1
            product_variants = []
            product_images = []
            product_videos = []
            
            for item in raw["_embedded"]["gallery_media"]:
                if item["type"] == "image":
                    large_image = item.get("large", {}).get("href")
                    if large_image:
                        product_images.append(large_image)
                elif item["type"] == "video":
                    video = item["video"]["href"]
                    product_videos.append(video)
            
            
            for variant in raw["colorVariants"]:
                variant_id = variant["product_id"]
                variant_url = variant["product_url"] if "product_url" in variant else None
                variant_color = variant["color"]
                variant_size_variants = [
                    {
                        "sku": size["sku"],
                        "availability": size["availability"],
                        "availabilityStatus": size["availability_status"],
                        "size": size["size"]
                    } for size in variant["sizeVariants"]
                ]
                product_variants.append(
                    {
                        "variantId": variant_id,
                        "variantUrl": variant_url,
                        "variantColor": variant_color,
                        "variantSizeVariations": variant_size_variants
                    }
                )
                if variant_id == product_id:
                    for size_variant in variant_size_variants:
                        product_availability += size_variant["availability"]
            
            if product_availability > 0:
                product_availability_status = "IN_STOCK"
            
            return {
                "scrapedAt": datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(),
                "dataType": data_type,
                "productId": product_id,
                "productName": product_name,
                "productUrl": product_url,
                "productModelNumber": product_model_number,
                "productDescription": product_description,
                "productThirdPartyOptions": product_third_party_options,
                "productRating": product_rating,
                "productReviewCount": product_review_count,
                "productSentiments": product_general_customer_sentiments,
                "productBadges": product_badges,
                "productPrice": product_price,
                "productOriginalPrice": product_original_price,
                "productDiscountPercentage": product_discount_percentage,
                "productPriceCurrency": product_price_currency,
                "productAvailability": product_availability,
                "productAvailabilityStatus": product_availability_status,
                "productPurchaseLimit": product_purchase_limit,
                "productColor": product_color,
                "productCategory": product_category,
                "productCategorySlug": product_category_slug,
                "productGender": product_gender,
                "productTotalColors": product_total_colors,
                "productVariants": product_variants,
                "productImages": product_images,
                "productVideos": product_videos
            }
        else: # Parse Review
            product_id = raw["productId"]
            product_name = raw["productName"]
            product_url = raw["productUrl"]
            product_model_number = raw["productModelNumber"]
            review_id = raw["id"]
            review_user_nickname = raw["userNickname"]
            review_rating = raw["rating"]
            review_title = raw["title"]
            review_submission_time = raw["submissionTime"]
            review_text = raw["text"]
            review_likes = raw["positiveFeedbackCount"]
            review_dislikes = raw["negativeFeedbackCount"]
            review_product_color = raw.get("color")
            user_recommends_product = raw["isRecommended"]
            questions_answered_from_review = [
                {
                    "question": q["dimensionLabel"],
                    "answer": q["valueLabel"]
                } for q in raw["customQuestions"]
            ]
            review_rating_range = raw["ratingRange"]
            review_images = [img["normalUrl"] for img in raw["photos"]]
            review_badges = raw["badges"]
            review_locale = raw["locale"]
            
            return {
                "scrapedAt": datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(),
                "dataType": data_type,
                "productId": product_id,
                "productName": product_name,
                "productUrl": product_url,
                "productModelNumber": product_model_number,
                "reviewId": review_id,
                "reviewUserNickname": review_user_nickname,
                "reviewRating": review_rating,
                "reviewRatingRange": review_rating_range,
                "reviewTitle": review_title,
                "reviewSubmissionTime": review_submission_time,
                "reviewText": review_text,
                "reviewLikes": review_likes,
                "reviewDislikes": review_dislikes,
                "reviewProductColor": review_product_color,
                "reviewProductColor": review_product_color,
                "userRecommendsProduct": user_recommends_product,
                "questionsAnsweredFromReview": questions_answered_from_review,
                "reviewImages": review_images,
                "reviewBadges": review_badges,
                "reviewLocale": review_locale
            }

    @auto_retry(max_attempts=5)
    @wait_for_cookie_refresh
    async def make_get_request(self, url: str, operation_type: Literal["product_search", "product_detail", "review_page", "rating_page"], params: dict={}) -> dict:
        async with self.http_request_semaphore:
            req_url = url + ("?" if params else "") + urlencode(params)
            try:
                # signal: AbortSignal.timeout(30000)
                raw_data, status_code = await asyncio.wait_for(
                    self.http_client.evaluate(
                        r'async (url) => {const res = await fetch(url, {credentials: "include"}); return [await res.text(), res.status];}',
                        req_url
                    ),
                    timeout=30
                )
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                if status_code== 404 and operation_type == "product_detail":
                    return {}
                elif status_code == 404 and operation_type != "product_detail":
                    raise InvalidUrlError(f"INVALID URL: {url}")
                else:
                    # Actor.log.info(f"PARAMETERS: {params}")
                    # Actor.log.info(f"FAILED URL: {url}")
                    # Actor.log.info(f"Response: {res.text}")
                    # await Actor.exit()
                    raise AntiBotBlockError
            except asyncio.TimeoutError:
                # Actor.log.info(f"PARAMETERS: {params}")
                # Actor.log.error(f"FAILED URL: {url}")
                # Actor.log.info(f"Response: {res.text}")
                # await Actor.exit()
                raise AntiBotBlockError
            else:
                if status_code== 404 and operation_type == "product_detail":
                    return {}
                elif status_code == 404 and operation_type != "product_detail":
                    raise InvalidUrlError(f"INVALID URL: {url}")
                elif status_code >= 400:
                    raise AntiBotBlockError
                elif "cpr_chlge" in data:
                    # Actor.log.info(f"PARAMETERS: {params}")
                    # Actor.log.error(f"FAILED URL: {url}")
                    # Actor.log.info(f"Response: {res.text}")
                    # await Actor.exit()
                    raise AntiBotBlockError
                else:
                    # Actor.log.info(f"PARAMETERS: {params}")
                    # Actor.log.info(f"SUCCESSFUL URL: {url}")
                    return data

    async def get_product_data(self, product_url: str) -> None:
        async with self.product_detail_semaphore:
            # await asyncio.sleep(0.3)
            if not self.product_url_is_valid(product_url):
                Actor.log.warning(f"SKIPPING URL: {product_url}")
                Actor.log.warning("REASON: Invalid URL or country-URL mismatch.\n")
                return
            
            product_id = product_url.split(".html")[0].split("/")[-1]
            
            # Get product details
            product_details = await self.make_get_request(
                url=f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0]}/gw/prd/details/{product_id}/detail?sitePath={SITEPATHS[self.country]}",
                operation_type="product_detail",
            )
            if not product_details:
                if self.is_running:
                    Actor.log.error(f"❌ PRODUCT NOT FOUND: {product_url}")
                return
            
            product_model = product_details["model_number"]
            color_variants: list[dict] = [item for item in product_details["_embedded"]["color_variations"]]

            tasks = [
                self.make_get_request(
                    url=f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0] if self.country in SPECIAL_REGIONS else REGIONS_DOMAINS[self.country]}/api/models/{product_model}/reviews?limit=1&offset=0&sort={self.reviews_sort}&ratings=&includeLocales=*&bazaarVoiceLocale=en_US",
                    operation_type="review_page",
                ),  # First reviews page
                self.make_get_request(url=f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0] if self.country in SPECIAL_REGIONS else REGIONS_DOMAINS[self.country]}/api/models/{product_model}/ratings?includeLocales={REVIEWS_LOCALES[self.country]}&bazaarVoiceLocale={BAZAARVOICE_LOCALES[self.country]}", operation_type="rating_page"), # Rating data
                *[
                    self.make_get_request(
                        url=f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0] if self.country in SPECIAL_REGIONS else REGIONS_DOMAINS[self.country]}/api/products/{variant['product_id']}/cached/availability",
                        operation_type="product_detail",
                    )
                    for variant in color_variants
                ],  # Other variants' availability
            ]
            results = await asyncio.gather(*tasks)
            rev_data = results[0]
            ratings_data = results[1]
            color_variants_availabilities = results[2:]
            
            product_details["reviewsData"] = rev_data
            product_details["ratingsData"] = ratings_data
            for variant in color_variants:
                variant["sizeVariants"] = []
                for var in color_variants_availabilities:
                    var_id = var.get("id", "")
                    if var_id == variant["product_id"]:
                        variant["sizeVariants"].extend(var.get("variation_list", []))
                        break
            
            product_details["colorVariants"] = color_variants
            product_details["dataType"] = "product"
            
            await self.items_queue.put(product_details)

    async def get_product_reviews(self, product_url: str=None, product: dict=None) -> None:
        async with self.review_page_semaphore:
            if (not product_url and not product) or (len(self.seen_model_numbers) >= self.max_products and self.max_products > 0):
                return
            
            if product_url:
                if not self.product_url_is_valid(product_url):
                    Actor.log.warning(f"SKIPPING URL: {product_url}")
                    Actor.log.warning(f"REASON: Either invalid URL or country-URL mismatch.\n")
                    return
                
                product_id = product_url.split(".html")[0].split("/")[-1]
                product_details = await self.make_get_request(
                    url=f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0]}/gw/prd/details/{product_id}/detail?sitePath={SITEPATHS[self.country]}",
                    operation_type="product_detail",
                )
                if not product_details:
                    if self.is_running:
                        Actor.log.error(f"❌ PRODUCT NOT FOUND: {product_url}")
                    return
                
                model_number = product_details["model_number"]
            else:
                product_details = {}
                product_id = product["id"]
                model_number = product["modelNumber"]
            
            if model_number in self.seen_model_numbers:
                return
            else:
                self.seen_model_numbers.add(model_number)
            
            parameters = {
                "limit": 10,
                "offset": 0,
                "sort": self.reviews_sort,
                "includeLocales": "*" if self.scrape_reviews_from_all_languages else REVIEWS_LOCALES[self.country],
                "bazaarVoiceLocale": BAZAARVOICE_LOCALES[self.country]
            }
            base_reviews_url = f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0]}/api/models/{model_number}/reviews"
            page_scraped_reviews_count = 0
            reviews_limit_reached = False
            while True:
                await asyncio.sleep(0.5)
                if reviews_limit_reached:
                    break
                
                data = await self.make_get_request(url=base_reviews_url, params=parameters, operation_type="review_page")
                
                try:
                    reviews = data["reviews"]
                    if not reviews:
                        break
                except KeyError:
                    break
                
                for review in reviews:
                    review["productId"] = product_details.get("product_id") if product_details else product["id"]
                    review["productName"] = product_details.get("product_name") if product_details else product["title"]
                    review["productUrl"] = product_details.get("product_url") if product_details else product["url"]
                    review["productModelNumber"] = model_number
                    review["dataType"] = "review"
                    
                    if review["id"] in self.seen_review_ids:
                        continue
                    else:
                        self.seen_review_ids.add(review["id"])
                    
                    await self.items_queue.put(review)
                    page_scraped_reviews_count += 1
                    
                    if page_scraped_reviews_count >= self.max_reviews_per_product and self.max_reviews_per_product > 0:
                        reviews_limit_reached = True
                        break
                
                parameters["offset"] += parameters["limit"]

    async def scrape_products(self, search_url: str, mode: Literal["products", "reviews"]) -> None:
        tasks = []
        async with self.product_search_semaphore:
            if not self.search_url_is_valid(search_url):
                Actor.log.warning(f"SKIPPING URL: {search_url}")
                Actor.log.warning(f"REASON: Either invalid URL or country-URL mismatch.\n")
                return
            
            if self.mode == "reviews" and len(self.seen_model_numbers) >= self.max_products and self.max_products > 0:
                return
            
            if "?" in search_url:
                parameters_string_fmt = search_url.split("?")[-1].split("&")
                parameters = {param.split("=")[0]: unquote(param.split("=")[-1]) for param in parameters_string_fmt if param.split("=")[0] != "start"}
            else:
                parameters = {}
            
            index_name = quote(unquote(search_url.split(f"{REGIONS_DOMAINS[self.country]}/")[-1].split("?")[0]))
            if self.country in SPECIAL_REGIONS:
                base_search_api_url = f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0]}/plp-app/_next/data/{self.adidas_store_build_id}/{SPECIAL_REGIONS_SUFFIXES[self.country]}/{index_name}.json"
            else:
                base_search_api_url = f"https://www.{REGIONS_DOMAINS[self.country].split("/")[0]}/plp-app/_next/data/{self.adidas_store_build_id}/{index_name}.json"
            
            start = 0
            scraped_products_count = 0
            products_limit_reached = False
            visited_pages = set()
            while True:
                if products_limit_reached:
                    break
                
                if self.mode == "reviews" and len(self.seen_model_numbers) >= self.max_products and self.max_products > 0:
                    break
                
                try:
                    parameters["start"] = start
                    data = await self.make_get_request(base_search_api_url, params=parameters, operation_type="product_search")
                except InvalidUrlError:
                    pass
                
                try:
                    page_products = data["pageProps"]["products"]
                except KeyError:
                    break
                
                page_url = data["pageProps"]["fullUrl"]
                if page_url in visited_pages:
                    break
                else:
                    visited_pages.add(page_url)
                
                if not page_products:
                    break
                
                for product in page_products:
                    if product["id"] in self.seen_product_ids:
                        continue
                    else:
                        self.seen_product_ids.add(product["id"])
                    
                    if mode == "products":
                        tasks.append(asyncio.create_task(self.get_product_data(product_url=product["url"])))
                    else:
                        tasks.append(asyncio.create_task(self.get_product_reviews(product=product)))
                    
                    scraped_products_count += 1
                    
                    if scraped_products_count >= self.max_products_per_search and self.max_products_per_search > 0:
                        products_limit_reached = True
                        break
                
                start += 48
            
            if tasks:
                await asyncio.gather(*tasks)

    async def get_items(self) -> AsyncGenerator[dict, None]:
        tasks = []

        async def run_all_tasks():
            try:
                await asyncio.gather(*tasks)
            except Exception as e:
                await self.items_queue.put(e)
            else:
                await self.items_queue.put("done")
        
        if not self.scrape_entire_website:
            for url in self.search_urls:
                if self.search_url_is_valid(url):
                    if self.mode == "products" or ((self.mode == "reviews" and len(self.seen_model_numbers) < self.max_products) or (self.mode == "reviews" and self.max_products == 0)):
                        tasks.append(self.scrape_products(search_url=url, mode=self.mode))
                else:
                    Actor.log.warning(f"SKIPPING URL: {url}")
                    Actor.log.warning("REASON: Invalid URL or country-URL mismatch.")
                    Actor.log.warning("Make sure your URL has no typos and that it matches your selected country.")
            
            for url in self.product_urls:
                if self.product_url_is_valid(url):
                    if self.mode == "products" or ((self.mode == "reviews" and len(self.seen_model_numbers) < self.max_products) or (self.mode == "reviews" and self.max_products == 0)):
                        tasks.append(self.get_product_data(product_url=url) if self.mode == "products" else self.get_product_reviews(product_url=url))
                else:
                    Actor.log.warning(f"SKIPPING URL: {url}")
                    Actor.log.warning("REASON: Invalid URL or country-URL mismatch.")
                    Actor.log.warning("Make sure your URL has no typos and that it matches your selected country.")
        else:
            search_url = f"https://www.{REGIONS_DOMAINS[self.country]}/search?q="
            self.max_products = 0
            self.max_products_per_search = 0
            if self.mode == "products" or ((self.mode == "reviews" and len(self.seen_model_numbers) < self.max_products) or (self.mode == "reviews" and self.max_products == 0)):
                tasks.append(self.scrape_products(search_url=search_url, mode=self.mode))
        
        if not tasks and not self.product_urls and not self.search_urls and not self.scrape_entire_website:
            self.max_products = 10
            self.max_reviews = 10
            search_url = f"https://www.{REGIONS_DOMAINS[self.country]}/search?q="
            
            if self.mode == "products" or ((self.mode == "reviews" and len(self.seen_model_numbers) < self.max_products) or (self.mode == "reviews" and self.max_products == 0)):
                tasks.append(self.scrape_products(search_url=search_url, mode=self.mode))
        
        runner = asyncio.create_task(run_all_tasks())
        
        try:
            while True:
                item = await self.items_queue.get()
                
                if isinstance(item, Exception):
                    self.is_running = False
                    raise item
                
                if item == "done":
                    self.is_running = False
                    break
                else:
                    data_type = item["dataType"]
                    if (self.scraped_products_count >= self.max_products and self.max_products > 0) or (self.scraped_reviews_count >= self.max_reviews and self.max_reviews > 0):
                        self.is_running = False
                        break
                    else:
                        if data_type == "product":
                            self.scraped_products_count += 1
                        else:
                            self.scraped_reviews_count += 1
                    
                    yield self.parse_item(item)
        finally:
            await self.context.close()
            runner.cancel()
