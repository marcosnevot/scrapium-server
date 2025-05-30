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
        self.stop_event = None  # Será asignado desde fuera (app.py)

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

    def set_stop_event(self, event) -> None:
        """Inyecta el threading.Event para cancelación desde fuera"""
        self.stop_event = event

    def run(self) -> Dict[str, any]:
        """
        Modo batch: devuelve JSON con event_info y tickets.
        No cancela reservas automáticamente.
        """
        event_info = self._scrape_event_info()
        resultados: Dict[str, int] = {}
        for tier in self._discover_tiers():
            if not tier.id_:
                resultados[tier.name] = 0
                continue
            resultados[tier.name] = self._count_stock_for_tier(tier.id_)
        self.driver.quit()
        return {"event_info": event_info, "tickets": resultados}

    def _count_stock_for_tier(self, select_id: str) -> int:
        """
        Cuenta de forma síncrona sin abrir pestañas extra ni cancelar reservas.
        """
        total = 0
        while True:
            self.driver.get(self.url)
            try:
                sel = self.wait.until(EC.presence_of_element_located((By.ID, select_id)))
            except TimeoutException:
                break
            opts = [int(o.get_attribute("value")) for o in Select(sel).options
                    if o.get_attribute("value").isdigit() and int(o.get_attribute("value")) > 0]
            if not opts:
                break
            qty = max(opts)
            Select(sel).select_by_value(str(qty))
            btn = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, self.BTN_CSS)))
            try:
                btn.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].click();", btn)
            total += qty
            time.sleep(0.25)
        return total

    def run_stream(self) -> Generator[tuple[str, int], None, None]:
        """
        Modo streaming: emite (tier, stock) en tiempo real,
        luego '__complete__'; y en caso de interrupción o final,
        cancela todas las reservas.
        """
        # Pestaña de control inicial
        self.driver.get(self.url)
        control = self.driver.current_window_handle

        tiers = self._discover_tiers()
        all_reserve_handles: List[str] = []

        try:
            # Conteo por cada tier
            for tier in tiers:
                # Salir si se señala cancelación
                if self.stop_event and self.stop_event.is_set():
                    break

                name = tier.name
                if not tier.id_:
                    yield name, 0
                    continue

                stock = 0
                reserve_handles: List[str] = []

                while True:
                    if self.stop_event and self.stop_event.is_set():
                        break

                    # Volver a la página principal
                    self.driver.switch_to.window(control)
                    self.driver.get(self.url)

                    try:
                        sel = self.wait.until(EC.presence_of_element_located((By.ID, tier.id_)))
                    except TimeoutException:
                        break

                    # Opciones disponibles
                    options = [
                        int(opt.get_attribute("value")) for opt in Select(sel).options
                        if opt.get_attribute("value").isdigit() and int(opt.get_attribute("value")) > 0
                    ]
                    if not options:
                        break

                    qty = max(options)

                    # Abrir nueva pestaña y reservar
                    self.driver.execute_script("window.open('');")
                    handles = self.driver.window_handles
                    new_handle = [h for h in handles if h != control and h not in reserve_handles][-1]
                    self.driver.switch_to.window(new_handle)
                    self.driver.get(self.url)

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
                    all_reserve_handles.append(new_handle)
                    yield name, stock

                    time.sleep(0.25)

                # Emitir stock final de este tier
                yield name, stock

            # Señal de completado al frontend
            yield "__complete__", ""

        finally:
            # Cancelar todas las reservas iniciadas (tanto tras normal como interrupción)
            for handle in all_reserve_handles:
                try:
                    self.driver.switch_to.window(handle)

                    # Abrir modal de cancelación
                    cancel_link = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-bs-target='#cancel-purchase-modal']"))
                    )
                    try:
                        cancel_link.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", cancel_link)

                    # Esperar aparición del modal
                    self.wait.until(EC.visibility_of_element_located((By.ID, "cancel-purchase-modal")))

                    # Confirmar cancelación
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

                    # Esperar cierre del modal
                    self.wait.until(EC.invisibility_of_element_located((By.ID, "cancel-purchase-modal")))

                except Exception:
                    pass
                finally:
                    try:
                        self.driver.close()
                    except Exception:
                        pass

            # Regresar a la pestaña de control y cerrar todo
            try:
                self.driver.switch_to.window(control)
            except Exception:
                pass
            self.driver.quit()

    def _discover_tiers(self) -> List[TicketTier]:
        """Extrae todos los tiers (select ID y nombre)."""
        self.driver.get(self.url)
        tiers: List[TicketTier] = []
        for ticket in self.driver.find_elements(By.CSS_SELECTOR, self.TICKET_CSS):
            try:
                price_el = ticket.find_element(By.CSS_SELECTOR, ".ticket-price span")
                price_text = price_el.text.strip().replace("€", "").split(",")[0]
                name = f"Entradas de {price_text}€"
            except NoSuchElementException:
                name = "Tanda sin precio"
            sel = ticket.find_elements(By.CSS_SELECTOR, self.SELECT_CSS)
            sel_id = sel[0].get_attribute("id") if sel else None
            tiers.append(TicketTier(id_=sel_id, name=name))
        return tiers

    def _scrape_event_info(self) -> Dict[str, str]:
        """Extrae título, fecha, hora y organizador."""
        self.driver.get(self.url)
        return {
            "title": self.driver.find_element(By.CSS_SELECTOR, "h1.text-raro mark.bg-crunchy").text.strip(),
            "date":  self.driver.find_element(By.CSS_SELECTOR, ".icon-calendar").find_element(By.XPATH, "../../span[2]").text.strip(),
            "time":  self.driver.find_element(By.CSS_SELECTOR, ".icon-clock").find_element(By.XPATH, "../../span[2]").text.strip(),
            "organizer": self.driver.find_element(By.CSS_SELECTOR, ".organizer").text.strip(),
        }
