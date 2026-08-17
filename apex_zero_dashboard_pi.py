"""
APEX ZERO / Cyberpunk Core — pilote Raspberry Pi (materiel reel)
=================================================================

Ceci est le pendant "materiel" de Dashboard_simulator.py (qui reste tel
quel, inchange, comme demande — il continue a servir de banc de test sur
PC avec pygame). Meme format de trame, meme logique de seuils/criticite,
mais le rendu ne va plus vers une fenetre pygame : il va vers trois vrais
ecrans SPI + un vrai moteur pas a pas, via le GPIO du Raspberry Pi.

Materiel pilote ici :
  - CRT (jauges moteur, pages) ......... ILI9341 2.4" SPI, 240x320
  - Rond central (vitesse digitale) .... GC9A01 1.28" SPI rond, 240x240
  - Rond horloge ........................ GC9A01 1.28" SPI rond, 240x240
  - Aiguille de vitesse analogique ...... 28BYJ-48 + driver ULN2003

Entrees :
  - UART materiel (GPIO14/TXD, GPIO15/RXD, alias /dev/serial0) <-- trame
    telemetrie venant de l'Arduino Mega (apex_zero_can_translator.ino),
    branche sur TX1/RX1 du Mega.
  - Port serie USB (ex /dev/ttyACM0) <-- boitier de boutons Arduino
    (apex_zero_button_box.ino, deja fonctionnel, inchange).

Format de trame (identique a Dashboard_simulator.py, ne pas diverger) :
  $RPM:2500,SPD:87,GEAR:3,TEMP:22,ODO:184213,CONSO:7.4,
   WTEMP:88.0,OTEMP:95.0,BTEMP:60.0,BPRESS:0.8,OPRESS:3.5,WPRESS:1.1*XX\n
  XX = XOR hexa (2 chiffres) de tous les octets entre '$' et '*'.

------------------------------------------------------------------------
CABLAGE — a lire avant de brancher quoi que ce soit
------------------------------------------------------------------------

1) Bus SPI partage (SCLK/MOSI communs aux 3 ecrans, CS/DC/RST dedies) :
     Pi physical pin 19 (GPIO10, MOSI) -> SDI/MOSI des 3 ecrans
     Pi physical pin 23 (GPIO11, SCLK) -> SCK des 3 ecrans
     Pi 3V3 (pin 1 ou 17)              -> VCC + LED (backlight) des 3 ecrans
     Pi GND                            -> GND commun

   CRT (ILI9341) :
     CS  -> GPIO8  (CE0, pin 24)
     DC  -> GPIO24 (pin 18)
     RST -> GPIO25 (pin 22)

   Rond central (GC9A01, vitesse digitale) :
     CS  -> GPIO7  (CE1, pin 26)
     DC  -> GPIO23 (pin 16)
     RST -> GPIO27 (pin 13)

   Rond horloge (GC9A01) :
     CS  -> GPIO5  (pin 29, CS logiciel — le SPI0 du Pi n'a que 2 CS
                    materiels, deja pris par le CRT et le rond central)
     DC  -> GPIO22 (pin 15)
     RST -> GPIO17 (pin 11)

2) Moteur pas a pas 28BYJ-48 (aiguille de vitesse), via le driver ULN2003 :
     IN1 -> GPIO6  (pin 31)
     IN2 -> GPIO13 (pin 33)
     IN3 -> GPIO19 (pin 35)
     IN4 -> GPIO26 (pin 37)
     Alim moteur : 5V DEDIE (pas le 5V du Pi si d'autres consommateurs
     sont dessus — le 28BYJ-48 peut appeler ~200-300mA en pointe).
     GND du driver ULN2003 relie au GND commun du Pi.

3) UART materiel vers l'Arduino Mega (TX1/RX1 = pins 18/19 du Mega) :
     Pi GPIO14/TXD (pin 8)  -> RX1 (pin 19) du Mega
     Pi GPIO15/RXD (pin 10) <- TX1 (pin 18) du Mega, MAIS PAS DIRECT :
        le Mega est en logique 5V, le Pi en 3.3V (non tolerant 5V sur ses
        GPIO). Il FAUT un diviseur de tension ou un level-shifter sur
        cette ligne (Mega TX1 -> Pi RXD). Diviseur simple suffisant :
        Mega TX1 -- R1 1kOhm --+-- Pi RXD
                                |
                              R2 2kOhm
                                |
                               GND
        (~3.3V cote Pi). Le sens Pi TXD -> Mega RX1 n'a pas besoin de
        diviseur : le Mega lit un "3.3V logique haut" sans probleme.
     GND commun Pi <-> Mega OBLIGATOIRE.

     Sur le Pi, il faut liberer l'UART materiel complet (le Pi 4 le
     partage par defaut avec le Bluetooth) :
       - `sudo raspi-config` -> Interface Options -> Serial Port
         -> "login shell over serial" = NON, "serial port hardware" = OUI
       - ajouter `dtoverlay=disable-bt` dans /boot/firmware/config.txt
         pour rendre le vrai UART PL011 dispo sur GPIO14/15 (sinon on
         herite du mini-UART, moins stable en debit)
       - `sudo systemctl disable hciuart`
       - rebooter, puis verifier que /dev/serial0 pointe vers ttyAMA0

4) Boitier de boutons : simplement en USB (cable data), apparait en
   /dev/ttyACM0 ou /dev/ttyUSB0 selon la carte Arduino utilisee.

------------------------------------------------------------------------
DEPENDANCES (a installer sur le Pi)
------------------------------------------------------------------------
  sudo apt install -y python3-pip python3-pil libjpeg-dev
  pip3 install adafruit-blinka adafruit-circuitpython-rgb-display \
               adafruit-circuitpython-gc9a01 pyserial RPi.GPIO

------------------------------------------------------------------------
LANCEMENT
------------------------------------------------------------------------
  python3 apex_zero_dashboard_pi.py \
      --telemetry-port /dev/serial0 --telemetry-baud 38400 \
      --buttons-port /dev/ttyACM0  --buttons-baud 115200
"""

import argparse
import math
import threading
import time
from datetime import datetime

import serial
import digitalio
import board

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Pilotes d'ecran — imports faits ici, avec message clair s'ils manquent
# ---------------------------------------------------------------------------
try:
    from adafruit_rgb_display import ili9341
except ImportError:
    ili9341 = None

try:
    from gc9a01 import GC9A01
except ImportError:
    GC9A01 = None
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


# ---------------------------------------------------------------------------
# Trame : identique bit pour bit a Dashboard_simulator.py — NE PAS DIVERGER
# ---------------------------------------------------------------------------

def parse_trame(line: str):
    line = line.strip()
    if not line.startswith("$") or "*" not in line:
        return None
    try:
        body, chk = line[1:].split("*")
        expected = int(chk, 16)
    except ValueError:
        return None
    actual = 0
    for ch in body:
        actual ^= ord(ch)
    if actual != expected:
        return None
    result = {}
    for field in body.split(","):
        if ":" not in field:
            continue
        key, val = field.split(":", 1)
        try:
            result[key] = float(val) if "." in val else int(val)
        except ValueError:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Seuils / jauges — repris de Dashboard_simulator.py pour rester coherent
# avec le simulateur (memes couleurs d'alerte, meme logique de defaut
# critique) ; valeurs de depart, a ajuster une fois le moteur reel connu.
# ---------------------------------------------------------------------------

GAUGE_ORDER = ["WTEMP", "OTEMP", "BTEMP", "BPRESS", "OPRESS", "WPRESS"]
GAUGES = {
    "WTEMP":  {"label": "TEMP. EAU",    "unit": "C",   "warn_high": 105},
    "OTEMP":  {"label": "TEMP. HUILE",  "unit": "C",   "warn_high": 130},
    "BTEMP":  {"label": "TEMP. TURBO",  "unit": "C",   "warn_high": 150},
    "BPRESS": {"label": "PRESS. TURBO", "unit": "bar", "warn_high": 1.8},
    "OPRESS": {"label": "PRESS. HUILE", "unit": "bar", "warn_low": 1.0},
    "WPRESS": {"label": "PRESS. EAU",   "unit": "bar", "warn_low": 0.5, "warn_high": 1.6},
}
CRITICAL_FIELDS = ["WTEMP", "OPRESS", "BTEMP", "BPRESS"]
CRITICAL_EXIT_MARGIN = {"WTEMP": 5, "OPRESS": 0.3, "BTEMP": 10, "BPRESS": 0.2}
CRITICAL_MESSAGES = {
    "WTEMP": "SURCHAUFFE EAU", "OPRESS": "PRESSION HUILE",
    "BTEMP": "SURCHAUFFE TURBO", "BPRESS": "SURPRESSION TURBO",
}
PAGES = ["MOTEUR", "ORDINATEUR DE BORD", "PERFORMANCE", "SYSTEME"]
MAX_SPEED = 300
MAX_RPM = 7000


def _gauge_is_warning(cfg, val):
    if "warn_high" in cfg and val >= cfg["warn_high"]:
        return True
    if "warn_low" in cfg and val <= cfg["warn_low"]:
        return True
    return False


def _critical_exit_value(key, cfg):
    margin = CRITICAL_EXIT_MARGIN.get(key, 0)
    if "warn_high" in cfg:
        return cfg["warn_high"] - margin
    return cfg["warn_low"] + margin


def update_critical_state(state, active_key):
    if active_key is not None:
        cfg = GAUGES[active_key]
        val = state.get(active_key)
        if val is None:
            return active_key
        exit_val = _critical_exit_value(active_key, cfg)
        if "warn_high" in cfg and val < exit_val:
            return None
        if "warn_low" in cfg and val > exit_val:
            return None
        return active_key
    for key in CRITICAL_FIELDS:
        cfg = GAUGES[key]
        val = state.get(key)
        if val is not None and _gauge_is_warning(cfg, val):
            return key
    return None


class TripComputer:
    def __init__(self):
        self.start_time = time.time()
        self.dist_km = 0.0
        self.fuel_used_l = 0.0
        self.speed_sum = 0.0
        self.speed_count = 0

    def update(self, dt, state):
        spd = state.get("SPD", 0) or 0
        conso = state.get("CONSO", 0) or 0
        dist_tick = spd * dt / 3600
        self.dist_km += dist_tick
        self.fuel_used_l += conso * dist_tick / 100
        self.speed_sum += spd
        self.speed_count += 1

    def avg_speed(self):
        return self.speed_sum / self.speed_count if self.speed_count else 0

    def avg_conso(self):
        return (self.fuel_used_l / self.dist_km * 100) if self.dist_km > 0.05 else 0

    def elapsed_str(self):
        secs = int(time.time() - self.start_time)
        return "{:02d}:{:02d}:{:02d}".format(secs // 3600, (secs % 3600) // 60, secs % 60)


# ---------------------------------------------------------------------------
# Moteur pas a pas 28BYJ-48 / ULN2003 — aiguille de vitesse analogique
# ---------------------------------------------------------------------------

# Sequence demi-pas (8 etapes), couple plus regulier qu'en pas complet.
HALF_STEP_SEQ = [
    [1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
    [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1],
]

# 28BYJ-48 : ~2048 demi-pas/tour en sortie de reducteur pour la plupart
# des unites vendues (ratio annonce 64:1, ratio reel mesure ~63.68:1 sur
# beaucoup d'exemplaires -> a affiner si l'aiguille derive dans le temps).
STEPS_PER_REV = 2048

# Course angulaire de l'aiguille (comme un cadran 190E : ~270 degres du
# 0 au MAX_SPEED), a adapter au dessin du cadran final.
NEEDLE_SWEEP_DEG = 270
NEEDLE_START_DEG = -135  # position "0 km/h" par rapport au haut du cadran


class StepperNeedle:
    """Pilote un 28BYJ-48 en tache de fond pour suivre une valeur cible
    (vitesse lissee) sans bloquer la boucle de rendu. Non-bloquant : on
    met a jour target_speed depuis le thread principal, un thread dedie
    fait avancer le moteur pas par pas vers la position correspondante."""

    def __init__(self, pins, min_step_delay=0.0015):
        self.pins = pins  # (IN1, IN2, IN3, IN4)
        self.min_step_delay = min_step_delay
        self.current_step = 0
        self.target_step = 0
        self._seq_idx = 0
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        if GPIO is not None:
            GPIO.setmode(GPIO.BCM)
            for p in self.pins:
                GPIO.setup(p, GPIO.OUT, initial=GPIO.LOW)

    def _write_seq(self, idx):
        if GPIO is None:
            return
        for pin, val in zip(self.pins, HALF_STEP_SEQ[idx % 8]):
            GPIO.output(pin, val)

    def set_speed(self, speed_kmh):
        speed_kmh = max(0, min(MAX_SPEED, speed_kmh))
        angle = NEEDLE_START_DEG + (speed_kmh / MAX_SPEED) * NEEDLE_SWEEP_DEG
        step = int(angle / 360.0 * STEPS_PER_REV)
        with self._lock:
            self.target_step = step

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if GPIO is not None:
            for p in self.pins:
                GPIO.output(p, GPIO.LOW)

    def _run(self):
        while self._running:
            with self._lock:
                target = self.target_step
            if self.current_step < target:
                self.current_step += 1
                self._seq_idx += 1
                self._write_seq(self._seq_idx)
            elif self.current_step > target:
                self.current_step -= 1
                self._seq_idx -= 1
                self._write_seq(self._seq_idx)
            else:
                time.sleep(0.02)
                continue
            time.sleep(self.min_step_delay)


# ---------------------------------------------------------------------------
# Ecrans SPI
# ---------------------------------------------------------------------------

class SoftCS:
    """CS logiciel pour le second GC9A01 (le SPI0 du Pi n'a que CE0/CE1
    materiels). adafruit_rgb_display/gc9a01 acceptent un objet
    digitalio-like pour 'cs' : on lui donne un DigitalInOut standard sur
    un GPIO libre, ce qui suffit ici (pas besoin de vrai CS materiel,
    la lib pilote elle-meme la broche avant/apres chaque transaction)."""
    pass  # digitalio.DigitalInOut fait deja le travail, cette classe
          # ne sert que de rappel documentaire.


def setup_screens(args):
    if ili9341 is None or GC9A01 is None:
    raise RuntimeError(
        "Librairies d'ecran manquantes. Installer : "
        "adafruit-circuitpython-rgb-display (et verifier gc9a01.py present)")
    spi = board.SPI()

    crt_cs = digitalio.DigitalInOut(board.CE0)
    crt_dc = digitalio.DigitalInOut(board.D24)
    crt_rst = digitalio.DigitalInOut(board.D25)
    crt = ili9341.ILI9341(
        spi, cs=crt_cs, dc=crt_dc, rst=crt_rst,
        width=240, height=320, rotation=90, baudrate=32000000,
    )

    speedo_cs = digitalio.DigitalInOut(board.CE1)
    speedo_dc = digitalio.DigitalInOut(board.D23)
    speedo_rst = digitalio.DigitalInOut(board.D27)
    speedo = GC9A01(
        spi, cs=speedo_cs, dc=speedo_dc, rst=speedo_rst,
        width=240, height=240, baudrate=32000000,
    )

    clock_cs = digitalio.DigitalInOut(board.D5)
    clock_dc = digitalio.DigitalInOut(board.D22)
    clock_rst = digitalio.DigitalInOut(board.D17)
    clock_scr = GC9A01(
        spi, cs=clock_cs, dc=clock_dc, rst=clock_rst,
        width=240, height=240, baudrate=32000000,
    )

    return crt, speedo, clock_scr


def load_font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


COLOR_BG = (8, 8, 10)
COLOR_FG = (220, 232, 245)
COLOR_ACCENT = (150, 215, 255)
COLOR_WARN = (255, 70, 70)
COLOR_MUTED = (130, 158, 195)
COLOR_DIGITAL = (230, 140, 40)


def draw_crt(state, page_idx, trip, critical_key, font_title, font_body, font_big):
    img = Image.new("RGB", (240, 320), COLOR_BG)
    d = ImageDraw.Draw(img)

    if critical_key is not None:
        d.rectangle((0, 0, 240, 320), fill=(60, 8, 8))
        d.text((20, 120), "! DEFAUT CRITIQUE !", font=font_title, fill=(255, 255, 255))
        d.text((20, 160), CRITICAL_MESSAGES.get(critical_key, critical_key),
                font=font_body, fill=(255, 255, 255))
        val = state.get(critical_key)
        if val is not None:
            d.text((20, 190), "{} {}".format(val, GAUGES[critical_key]["unit"]),
                    font=font_body, fill=(255, 255, 255))
        return img

    page = PAGES[page_idx]
    d.text((10, 8), page, font=font_title, fill=COLOR_ACCENT)
    d.line((10, 34, 230, 34), fill=COLOR_MUTED)

    if page == "MOTEUR":
        y = 50
        for key in GAUGE_ORDER:
            cfg = GAUGES[key]
            val = state.get(key)
            warn = val is not None and _gauge_is_warning(cfg, val)
            color = COLOR_WARN if warn else COLOR_FG
            txt = "{:<12}{}".format(cfg["label"], "--" if val is None else
                                     "{} {}".format(val, cfg["unit"]))
            d.text((10, y), txt, font=font_body, fill=color)
            y += 26
    elif page == "ORDINATEUR DE BORD":
        lines = [
            "Duree     {}".format(trip.elapsed_str()),
            "Distance  {:.1f} km".format(trip.dist_km),
            "V. moy    {:.0f} km/h".format(trip.avg_speed()),
            "Conso moy {:.1f} L/100".format(trip.avg_conso()),
        ]
        y = 50
        for line in lines:
            d.text((10, y), line, font=font_body, fill=COLOR_FG)
            y += 26
    elif page == "PERFORMANCE":
        d.text((10, 50), "V max  {} km/h".format(state.get("_max_spd", 0)),
                font=font_body, fill=COLOR_FG)
        d.text((10, 76), "Regime max  {} tr/min".format(state.get("_max_rpm", 0)),
                font=font_body, fill=COLOR_FG)
    elif page == "SYSTEME":
        d.text((10, 50), "Lien telemetrie: {}".format(state.get("_link_status", "?")),
                font=font_body, fill=COLOR_FG)
        d.text((10, 76), "Trames recues: {}".format(state.get("_frame_count", 0)),
                font=font_body, fill=COLOR_FG)

    return img


def draw_speedo(state, spd_smooth, rpm_smooth, font_big, font_small):
    img = Image.new("RGB", (240, 240), COLOR_BG)
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 236, 236), outline=COLOR_MUTED, width=2)

    spd_txt = "{:.0f}".format(spd_smooth)
    bbox = font_big.getbbox(spd_txt)
    w = bbox[2] - bbox[0]
    d.text((120 - w / 2, 80), spd_txt, font=font_big, fill=COLOR_DIGITAL)
    d.text((100, 150), "km/h", font=font_small, fill=COLOR_MUTED)

    gear = state.get("GEAR", "-")
    d.text((110, 40), str(gear), font=font_small, fill=COLOR_FG)
    d.text((70, 180), "{:.0f} tr/min".format(rpm_smooth), font=font_small, fill=COLOR_MUTED)
    return img


def draw_clock(font_small):
    img = Image.new("RGB", (240, 240), COLOR_BG)
    d = ImageDraw.Draw(img)
    cx, cy, r = 120, 120, 110
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=COLOR_MUTED, width=2)

    now = datetime.now()
    for unit, length, width, val, maxval in (
        ("h", 55, 5, now.hour % 12 + now.minute / 60, 12),
        ("m", 80, 3, now.minute, 60),
        ("s", 90, 1, now.second, 60),
    ):
        angle = math.radians(val / maxval * 360 - 90)
        x2 = cx + length * math.cos(angle)
        y2 = cy + length * math.sin(angle)
        color = COLOR_WARN if unit == "s" else COLOR_DIGITAL
        d.line((cx, cy, x2, y2), fill=color, width=width)

    txt = now.strftime("%H:%M")
    bbox = font_small.getbbox(txt)
    w = bbox[2] - bbox[0]
    d.text((cx - w / 2, cy + 40), txt, font=font_small, fill=COLOR_FG)
    return img


# ---------------------------------------------------------------------------
# Lecture serie — telemetrie (Mega, UART materiel) et boitier de boutons
# (USB), chacun dans son thread, comme dans Dashboard_simulator.py
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.last_rx_time = None
        self.frame_count = 0
        self.max_spd = 0
        self.max_rpm = 0
        self.button_events = []  # file d'evenements (name, is_long)

    def update_telemetry(self, parsed):
        with self.lock:
            self.data.update(parsed)
            self.last_rx_time = time.time()
            self.frame_count += 1
            self.max_spd = max(self.max_spd, parsed.get("SPD", 0) or 0)
            self.max_rpm = max(self.max_rpm, parsed.get("RPM", 0) or 0)

    def push_button(self, name, is_long):
        with self.lock:
            self.button_events.append((name, is_long))

    def pop_buttons(self):
        with self.lock:
            events, self.button_events = self.button_events, []
            return events

    def snapshot(self):
        with self.lock:
            snap = dict(self.data)
            snap["_link_status"] = "OK" if (
                self.last_rx_time and time.time() - self.last_rx_time < 2.0) else "PERDUE"
            snap["_frame_count"] = self.frame_count
            snap["_max_spd"] = self.max_spd
            snap["_max_rpm"] = self.max_rpm
            return snap


def telemetry_reader(port, baud, shared, stop_evt):
    buf = ""
    conn = None
    while not stop_evt.is_set():
        if conn is None:
            try:
                conn = serial.Serial(port, baud, timeout=0.05)
                print("Telemetrie: connecte sur", port)
            except Exception as exc:
                print("Telemetrie: attente port ({}) - {}".format(port, exc))
                time.sleep(1.0)
                continue
        try:
            chunk = conn.read(conn.in_waiting or 1)
            if chunk:
                buf += chunk.decode(errors="ignore")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    parsed = parse_trame(line)
                    if parsed:
                        shared.update_telemetry(parsed)
        except Exception as exc:
            print("Telemetrie: erreur lecture -", exc)
            conn = None
            time.sleep(0.5)


def buttons_reader(port, baud, shared, stop_evt):
    buf = ""
    conn = None
    while not stop_evt.is_set():
        if conn is None:
            try:
                conn = serial.Serial(port, baud, timeout=0.05)
                print("Boutons: connecte sur", port)
            except Exception as exc:
                print("Boutons: attente port ({}) - {}".format(port, exc))
                time.sleep(1.0)
                continue
        try:
            chunk = conn.read(conn.in_waiting or 1)
            if chunk:
                buf += chunk.decode(errors="ignore")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("BTN:"):
                        continue
                    payload = line[4:]
                    is_long = payload.endswith("_LONG")
                    name = payload[:-5] if is_long else payload
                    shared.push_button(name, is_long)
        except Exception as exc:
            print("Boutons: erreur lecture -", exc)
            conn = None
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telemetry-port", default="/dev/serial0",
                     help="UART materiel relie au Mega (TX1/RX1)")
    ap.add_argument("--telemetry-baud", type=int, default=38400)
    ap.add_argument("--buttons-port", default="/dev/ttyACM0",
                     help="Port USB du boitier de boutons Arduino")
    ap.add_argument("--buttons-baud", type=int, default=115200)
    ap.add_argument("--fps", type=int, default=15)
    args = ap.parse_args()

    shared = SharedState()
    stop_evt = threading.Event()

    t1 = threading.Thread(target=telemetry_reader,
                           args=(args.telemetry_port, args.telemetry_baud, shared, stop_evt),
                           daemon=True)
    t2 = threading.Thread(target=buttons_reader,
                           args=(args.buttons_port, args.buttons_baud, shared, stop_evt),
                           daemon=True)
    t1.start()
    t2.start()

    needle = StepperNeedle(pins=(6, 13, 19, 26))
    needle.start()

    crt_scr, speedo_scr, clock_scr = setup_screens(args)

    font_title = load_font(20)
    font_body = load_font(16)
    font_big = load_font(56)
    font_small = load_font(18)

    trip = TripComputer()
    page_idx = 0
    critical_key = None
    critical_acked_key = None
    disp_spd = 0.0
    disp_rpm = 0.0
    smooth_tau = 0.12
    last_t = time.time()
    last_clock_update = 0

    print("Dashboard demarre. Ctrl+C pour arreter.")
    try:
        while True:
            now = time.time()
            dt = now - last_t
            last_t = now

            state = shared.snapshot()
            trip.update(dt, state)

            critical_key = update_critical_state(state, critical_key)
            if critical_key is None:
                critical_acked_key = None
            display_critical = critical_key if critical_key != critical_acked_key else None

            for name, is_long in shared.pop_buttons():
                if name == "LEFT":
                    if is_long:
                        critical_acked_key = critical_key
                    else:
                        page_idx = (page_idx - 1) % len(PAGES)
                elif name == "RIGHT":
                    if is_long:
                        critical_acked_key = critical_key
                    else:
                        page_idx = (page_idx + 1) % len(PAGES)
                # UP/DOWN/OK/CENTER : reserves pour une future page dediee
                # sur le rond central, non geres ici pour l'instant.

            alpha = 1 - math.exp(-dt / smooth_tau) if dt > 0 else 1.0
            disp_spd += (state.get("SPD", 0) - disp_spd) * alpha
            disp_rpm += (state.get("RPM", 0) - disp_rpm) * alpha
            needle.set_speed(disp_spd)

            crt_img = draw_crt(state, page_idx, trip, display_critical,
                                font_title, font_body, font_big)
            crt_scr.image(crt_img)

            speedo_img = draw_speedo(state, disp_spd, disp_rpm, font_big, font_small)
            speedo_scr.image(speedo_img)

            if now - last_clock_update > 1.0:
                clock_scr.image(draw_clock(font_small))
                last_clock_update = now

            time.sleep(max(0, 1.0 / args.fps - (time.time() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        needle.stop()
        if GPIO is not None:
            GPIO.cleanup()


if __name__ == "__main__":
    main()
