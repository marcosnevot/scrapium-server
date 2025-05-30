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
        opts.binary_location = os.environ.get(
            "CHROME_BIN", "/usr/bin/chromium")
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")

        chromedriver_path = os.environ.get(
            "CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
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
        return {"event_info": event_info, "tickets": resultados}

    def run_stream(self) -> Generator[tuple[str, int], None, None]:
        # 1. Abrir página inicial en pestaña de control
        self.driver.get(self.url)
        control_handle = self.driver.current_window_handle

        tiers = self._discover_tiers()
        for tier in tiers:
            if getattr(self, "stop_event", None) and self.stop_event.is_set():
                break

            name = tier.name
            if not tier.id_:
                yield name, 0
                continue

            stock = 0
            reserve_handles: List[str] = []

            # 2. Reservar en pestañas separadas
            while True:
                if getattr(self, "stop_event", None) and self.stop_event.is_set():
                    break

                # Volver a pestaña de control
                self.driver.switch_to.window(control_handle)
                self.driver.get(self.url)

                try:
                    sel_el = self.wait.until(
                        EC.presence_of_element_located((By.ID, tier.id_))
                    )
                except TimeoutException:
                    break

                select = Select(sel_el)
                opts = [int(o.get_attribute("value")) for o in select.options if (v := o.get_attribute("value")).isdigit() and int(v) > 0]
                if not opts:
                    break

                qty = max(opts)
                # Abrir nueva pestaña para reserva
                self.driver.execute_script("window.open('');")
                new_handle = [h for h in self.driver.window_handles if h != control_handle and h not in reserve_handles][-1]
                self.driver.switch_to.window(new_handle)
                self.driver.get(self.url)

                # Seleccionar y reservar
                sel2 = self.wait.until(EC.presence_of_element_located((By.ID, tier.id_)))
                Select(sel2).select_by_value(str(qty))
                btn = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, self.BTN_CSS)))
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", btn)

                stock += qty
                yield name, stock
                reserve_handles.append(new_handle)
                time.sleep(0.25)

            # 3. Cancelar todas las compras reservadas
            for handle in reserve_handles:
                self.driver.switch_to.window(handle)
                try:
                    # Click en enlace que abre el modal
                    cancel_btn = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-bs-target='#cancel-purchase-modal']"))
                    )
                    cancel_btn.click()
                    # Esperar animación y hacer click en confirmación
                    confirm_btn = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "#cancel-purchase-modal .modal-footer a[rel='nofollow'][data-method='post']"))
                    )
                    confirm_btn.click()
                    # Esperar a que el modal desaparezca
                    self.wait.until(
                        EC.invisibility_of_element_located((By.ID, "cancel-purchase-modal"))
                    )
                except TimeoutException:
                    pass
                # Cerrar pestaña
                self.driver.close()

            # Volver a pestaña de control
            self.driver.switch_to.window(control_handle)
            yield name, stock

        # 4. Cerrar navegador
        self.driver.quit()

    def _discover_tiers(self) -> List[TicketTier]:
        self.driver.get(self.url)
        tiers: List[TicketTier] = []
        for ticket in self.driver.find_elements(By.CSS_SELECTOR, self.TICKET_CSS):
            try:
                price_el = ticket.find_element(
                    By.CSS_SELECTOR, ".ticket-price span")
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
        """
        Conteo batch: igual que run_stream pero acumulado,
        y también respeta scraper.stop_event.
        """
        stock = 0
        while True:
            if getattr(self, "stop_event", None) and self.stop_event.is_set():
                break

            self.driver.get(self.url)
            try:
                sel_el = self.wait.until(
                    EC.presence_of_element_located((By.ID, select_id))
                )
            except TimeoutException:
                break

            select = Select(sel_el)
            opts = []
            for opt in select.options:
                v = opt.get_attribute("value")
                if v.isdigit() and int(v) > 0:
                    opts.append(int(v))
            if not opts:
                break

            qty = max(opts)
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
        event_title = self.driver.find_element(
            By.CSS_SELECTOR, "h1.text-raro mark.bg-crunchy").text.strip()
        date = self.driver.find_element(
            By.CSS_SELECTOR, ".icon-calendar").find_element(By.XPATH, "../../span[2]").text.strip()
        time_event = self.driver.find_element(
            By.CSS_SELECTOR, ".icon-clock").find_element(By.XPATH, "../../span[2]").text.strip()
        organizer = self.driver.find_element(
            By.CSS_SELECTOR, ".organizer").text.strip()
        return {
            "title": event_title,
            "date": date,
            "time": time_event,
            "organizer": organizer
        }
