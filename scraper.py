from __future__ import annotations
import os
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException
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

        service = Service(executable_path=os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"))
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.wait = WebDriverWait(self.driver, timeout)

    def run(self) -> Dict[str, any]:
        """
        Batch mode: hace exactamente lo mismo que run_stream pero
        devuelve un dict al final en lugar de hacer yield.
        Atención: este modo también reservará entradas y
        no las cancelará automáticamente.
        """
        info = self._scrape_event_info()
        resultado: Dict[str, int] = {}
        for tier in self._discover_tiers():
            if not tier.id_:
                resultado[tier.name] = 0
                continue
            # reutilizamos el método de streaming para contar y cancelar
            total = 0
            for _, stock in self._count_and_cancel_one_tier(tier):
                total = stock
            resultado[tier.name] = total
        self.driver.quit()
        return {"event_info": info, "tickets": resultado}

    def run_stream(self) -> Generator[tuple[str, int], None, None]:
        """
        Streaming mode: por cada actualización emite (tier_name, stock_total).
        Al final de cada tier, cancela todas las pestañas abiertas para liberar
        las reservas y cierra el navegador al concluir todo.
        """
        # 1) pestaña de control
        self.driver.get(self.url)
        control = self.driver.current_window_handle

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

            # 2) por cada tanda, abrir pestaña y reservar
            while True:
                if getattr(self, "stop_event", None) and self.stop_event.is_set():
                    break

                # volvemos a pestaña principal y recargamos
                self.driver.switch_to.window(control)
                self.driver.get(self.url)

                try:
                    sel = self.wait.until(EC.presence_of_element_located((By.ID, tier.id_)))
                except TimeoutException:
                    break

                opts = [int(o.get_attribute("value")) for o in Select(sel).options
                        if o.get_attribute("value").isdigit() and int(o.get_attribute("value")) > 0]
                if not opts:
                    break

                qty = max(opts)

                # abrimos nueva pestaña
                self.driver.execute_script("window.open('');")
                all_handles = self.driver.window_handles
                new_handle = [h for h in all_handles if h != control and h not in reserve_handles][-1]
                self.driver.switch_to.window(new_handle)
                self.driver.get(self.url)

                # seleccionamos y clicamos continuar
                sel2 = self.wait.until(EC.presence_of_element_located((By.ID, tier.id_)))
                Select(sel2).select_by_value(str(qty))
                btn = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, self.BTN_CSS)))
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", btn)

                stock += qty
                reserve_handles.append(new_handle)
                yield name, stock

                time.sleep(0.25)

            # 3) cancelar todas las reservas abiertas
            for handle in reserve_handles:
                self.driver.switch_to.window(handle)
                try:
                    # abrir modal
                    cancel_link = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-bs-target='#cancel-purchase-modal']"))
                    )
                    try:
                        cancel_link.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", cancel_link)

                    # esperar a que el modal sea visible
                    self.wait.until(EC.visibility_of_element_located((By.ID, "cancel-purchase-modal")))

                    # clicar el botón definitivo dentro del modal
                    confirm = self.wait.until(
                        EC.element_to_be_clickable((
                            By.CSS_SELECTOR,
                            "#cancel-purchase-modal .modal-footer a[rel='nofollow'][data-method='post']"
                        ))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", confirm)
                    try:
                        confirm.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", confirm)

                    # esperar a que el modal desaparezca
                    self.wait.until(EC.invisibility_of_element_located((By.ID, "cancel-purchase-modal")))
                except (TimeoutException, NoSuchElementException):
                    # si algo falla, seguimos con la siguiente pestaña
                    pass
                finally:
                    # cerrar siempre la pestaña actual
                    self.driver.close()

            # devolvemos pestaña principal y emitimos stock final de este tier
            self.driver.switch_to.window(control)
            yield name, stock

        # 4) fin de todo: cerramos el navegador
        self.driver.quit()

    def _discover_tiers(self) -> List[TicketTier]:
        self.driver.get(self.url)
        tiers: List[TicketTier] = []
        for ticket in self.driver.find_elements(By.CSS_SELECTOR, self.TICKET_CSS):
            try:
                price_el = ticket.find_element(By.CSS_SELECTOR, ".ticket-price span")
                price_int = price_el.text.strip().replace("€", "").split(",")[0]
                name = f"Entradas de {price_int}€"
            except NoSuchElementException:
                name = "Tanda sin precio"

            sel = ticket.find_elements(By.CSS_SELECTOR, self.SELECT_CSS)
            sel_id = sel[0].get_attribute("id") if sel else None
            tiers.append(TicketTier(id_=sel_id, name=name))
        return tiers

    def _scrape_event_info(self) -> Dict[str, str]:
        self.driver.get(self.url)
        return {
            "title": self.driver.find_element(By.CSS_SELECTOR, "h1.text-raro mark.bg-crunchy").text.strip(),
            "date":  self.driver.find_element(By.CSS_SELECTOR, ".icon-calendar").find_element(By.XPATH, "../../span[2]").text.strip(),
            "time":  self.driver.find_element(By.CSS_SELECTOR, ".icon-clock").find_element(By.XPATH, "../../span[2]").text.strip(),
            "organizer": self.driver.find_element(By.CSS_SELECTOR, ".organizer").text.strip(),
        }
