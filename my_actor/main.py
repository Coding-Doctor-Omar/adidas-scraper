from __future__ import annotations
from .adidas_scraper import AdidasScraper
from apify.errors import ApifyApiError
from apify import Actor
import random


async def main() -> None:
    async with Actor:
        COUNTRY_CODES = {
            "united-states": "US",
            "united-kingdom": "GB",
            "canada-en": "CA",
            "canada-fr": "CA",
            "germany": "DE",
            "france": "FR",
            "netherlands": "NL",
            "spain": "ES",
            "italy": "IT",
            "japan": "JP",
            "australia": "AU",
            "new-zealand": "NZ",
        }
        DATA_TYPES = {
            "products": "product",
            "reviews": "review"
        }
        
        user_input = await Actor.get_input() or {}
        country = user_input.get("country")
        data_type = DATA_TYPES[user_input.get("mode")]
        country_code = COUNTRY_CODES[country]
        actor_running = True
        run_successful = True
        
        try:
            proxy_cfg = await Actor.create_proxy_configuration(groups=["RESIDENTIAL"], country_code=country_code)
            session_id = f"{random.randint(0, 999999)}"
            proxy_url = await proxy_cfg.new_url(session_id=session_id)
        except (AttributeError, ValueError):
            session_id = ""
            session_id = proxy_cfg = proxy_url = None
        
        batch = []
        scrape_count = 0
        try:
            adidas_scraper = AdidasScraper(options=user_input, session_id=session_id, proxy_cfg=proxy_cfg, proxy_url=proxy_url)
            await adidas_scraper.initialize()
            
            async for item in adidas_scraper.get_items():
                batch.append(item)
                scrape_count += 1
                Actor.log.info(f"Scraped {scrape_count} {data_type}(s).")
                
                if scrape_count % 10 == 0:
                    await Actor.push_data(batch, charged_event_name=data_type)
                    batch.clear()
        except ApifyApiError as e:
            Actor.log.error(f"Push failed. Type: {e.type}, Data: {e.data}")
            Actor.log.warning("Please report your error to the Actor developer by opening a new issue: https://apify.com/coding-doctor-omar/adidas-scraper/issues/open")
            actor_running = False
            adidas_scraper.is_running = False
            run_successful = False
            await Actor.exit()
        except Exception as e:
            adidas_scraper.is_running = False
            error = f"{type(e).__name__}: {e}"
            Actor.log.error(error)
            run_successful = False
            Actor.log.warning("Please report your error to the Actor developer by opening a new issue: https://apify.com/coding-doctor-omar/adidas-scraper/issues/open")
        finally:
            if batch and actor_running:
                await Actor.push_data(batch, charged_event_name=data_type)
                adidas_scraper.is_running = False
            
            if run_successful:
                Actor.log.info("Done! 🎉")
                Actor.log.info("If this Actor is helpful to you, consider leaving a 5-star review on Apify as this helps me a lot 👇\nhttps://console.apify.com/actors/HO6qdaJ2l9UnhjgnZ/reviews")
                await Actor.exit()