from __future__ import annotations
import os
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, ElementClickInterceptedException
)

@dataclass
class TicketTier:
    id_: Optional[str]
    name: str
    stock: int = 0

class EntradiumScraper:
    BTN_CSS = "button[type=submit].btn-dark:not([disabled])"
    SELECT_CSS = "select[id^='tickets_ticket_list'][id$='_qty']"
    TICKET_CSS = "div.ticket"

    def __init__(self, url: str, headless: bool = True, timeout: int = 10) -> None:
        self.url = url
        self.timeout = timeout

        opts = Options()
        opts.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")

        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
        service = Service(executable_path=chromedriver_path)

        self.driver = webdriver.Chrome(service=service, options=opts)
        self.wait = WebDriverWait(self.driver, timeout)

    def run(self) -> Dict[str, any]:
        event_info = self._scrape_event_info()
        resultados: Dict[str, int] = {}
        for tier in self._discover_tiers():
            tier.stock = (self._count_stock_for_tier(tier.id_) if tier.id_ else 0)
            resultados[tier.name] = tier.stock
        self.driver.quit()
        return {
            "event_info": event_info,
            "tickets": resultados
        }

    def run_stream(self) -> Generator[Tuple[str, int], None, None]:
        tiers = self._discover_tiers()
        for tier in tiers:
            name = tier.name
            if not tier.id_:
                yield name, 0
                continue

            stock = 0
            while True:
                self.driver.get(self.url)
                try:
                    sel_el = self.wait.until(
                        EC.presence_of_element_located((By.ID, tier.id_))
                    )
                except TimeoutException:
                    break

                select = Select(sel_el)
                opt_vals = [
                    int(o.get_attribute("value"))
                    for o in select.options
                    if (v := o.get_attribute("value")).isdigit() and int(v) > 0
                ]
                if not opt_vals:
                    break

                qty = max(opt_vals)
                try:
                    select.select_by_value(str(qty))
                except Exception:
                    break

                try:
                    btn = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, self.BTN_CSS))
                    )
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", btn
                    )
                    try:
                        btn.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", btn)
                except TimeoutException:
                    break

                stock += qty
                yield name, stock
                time.sleep(0.25)

            yield name, stock

        self.driver.quit()

    def _discover_tiers(self) -> List[TicketTier]:
        self.driver.get(self.url)
        tiers: List[TicketTier] = []
        for ticket in self.driver.find_elements(By.CSS_SELECTOR, self.TICKET_CSS):
            try:
                price_el = ticket.find_element(By.CSS_SELECTOR, ".ticket-price span")
                price_text = price_el.text.strip().replace("€", "").strip()
                price_int = price_text.split(",")[0]
                name = f"Entradas de {price_int}€"
            except NoSuchElementException:
                name = "Tanda sin precio"

            sel = ticket.find_elements(By.CSS_SELECTOR, self.SELECT_CSS)
            sel_id = sel[0].get_attribute("id") if sel else None
            tiers.append(TicketTier(id_=sel_id, name=name))
        return tiers

    def _count_stock_for_tier(self, select_id: str) -> int:
        stock = 0
        while True:
            self.driver.get(self.url)
            try:
                sel_el = self.wait.until(
                    EC.presence_of_element_located((By.ID, select_id))
                )
            except TimeoutException:
                break
            select = Select(sel_el)
            opt_vals = [
                int(o.get_attribute("value"))
                for o in select.options
                if (v := o.get_attribute("value")).isdigit() and int(v) > 0
            ]
            if not opt_vals:
                break
            qty = max(opt_vals)
            try:
                select.select_by_value(str(qty))
            except Exception:
                break
            try:
                btn = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, self.BTN_CSS))
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn
                )
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", btn)
            except TimeoutException:
                break
            stock += qty
            time.sleep(0.25)
        return stock

    def _scrape_event_info(self) -> Dict[str, str]:
        self.driver.get(self.url)
        event_title = self.driver.find_element(By.CSS_SELECTOR, "h1.text-raro mark.bg-crunchy").text.strip()
        date = self.driver.find_element(By.CSS_SELECTOR, ".icon-calendar").find_element(By.XPATH, "../../span[2]").text.strip()        
        time_event = self.driver.find_element(By.CSS_SELECTOR, ".icon-clock").find_element(By.XPATH, "../../span[2]").text.strip()
        organizer = self.driver.find_element(By.CSS_SELECTOR, ".organizer").text.strip()
        return {
            "title": event_title,
            "date": date,
            "time": time_event,
            "organizer": organizer
        }
