"""
gc9a01.py - driver GC9A01 autonome (bus SPI direct, sans dependance a
adafruit-circuitpython-gc9a01a qui est basee sur displayio et n'expose
pas la meme API que adafruit_rgb_display).

Garde volontairement la meme "forme" d'appel que l'ILI9341 utilise
ailleurs dans ce projet :
    GC9A01(spi, cs=.., dc=.., rst=.., width=.., height=.., baudrate=..)
    puis ecran.image(image_pil)
"""

import time

try:
    import numpy as np
except ImportError:
    np = None


class GC9A01:
    def __init__(self, spi, *, cs, dc, rst=None, width=240, height=240,
                 baudrate=24000000, rotation=0):
        self.spi = spi
        self.cs = cs
        self.dc = dc
        self.rst = rst
        self.width = width
        self.height = height
        self.baudrate = baudrate
        self.rotation = rotation

        self.cs.switch_to_output(value=True)
        self.dc.switch_to_output(value=False)
        if self.rst is not None:
            self.rst.switch_to_output(value=True)

        self._reset()
        self._init_sequence()

    # -- bas niveau -----------------------------------------------------

    def _reset(self):
        if self.rst is not None:
            self.rst.value = True
            time.sleep(0.005)
            self.rst.value = False
            time.sleep(0.02)
            self.rst.value = True
            time.sleep(0.15)

    def _lock_spi(self):
        while not self.spi.try_lock():
            pass
        self.spi.configure(baudrate=self.baudrate, polarity=0, phase=0)

    def _write_cmd(self, cmd, data=b""):
        self._lock_spi()
        try:
            self.cs.value = False
            self.dc.value = False
            self.spi.write(bytes([cmd]))
            if data:
                self.dc.value = True
                self.spi.write(bytes(data))
        finally:
            self.cs.value = True
            self.spi.unlock()

    def _write_pixels(self, data):
        self._lock_spi()
        try:
            self.cs.value = False
            self.dc.value = True
            self.spi.write(data)
        finally:
            self.cs.value = True
            self.spi.unlock()

    # -- sequence d'init GC9A01 (registres constructeur standards) ------

    def _init_sequence(self):
        self._write_cmd(0xEF)
        self._write_cmd(0xEB, b"\x14")
        self._write_cmd(0xFE)
        self._write_cmd(0xEF)
        self._write_cmd(0xEB, b"\x14")
        self._write_cmd(0x84, b"\x40")
        self._write_cmd(0x85, b"\xFF")
        self._write_cmd(0x86, b"\xFF")
        self._write_cmd(0x87, b"\xFF")
        self._write_cmd(0x88, b"\x0A")
        self._write_cmd(0x89, b"\x21")
        self._write_cmd(0x8A, b"\x00")
        self._write_cmd(0x8B, b"\x80")
        self._write_cmd(0x8C, b"\x01")
        self._write_cmd(0x8D, b"\x01")
        self._write_cmd(0x8E, b"\xFF")
        self._write_cmd(0x8F, b"\xFF")
        self._write_cmd(0xB6, b"\x00\x00")
        self._write_cmd(0x36, bytes([self._madctl_for_rotation()]))
        self._write_cmd(0x3A, b"\x05")  # COLMOD : 16 bits/pixel (RGB565)
        self._write_cmd(0x90, b"\x08\x08\x08\x08")
        self._write_cmd(0xBD, b"\x06")
        self._write_cmd(0xBC, b"\x00")
        self._write_cmd(0xFF, b"\x60\x01\x04")
        self._write_cmd(0xC3, b"\x13")
        self._write_cmd(0xC4, b"\x13")
        self._write_cmd(0xC9, b"\x22")
        self._write_cmd(0xBE, b"\x11")
        self._write_cmd(0xE1, b"\x10\x0E")
        self._write_cmd(0xDF, b"\x21\x0C\x02")
        self._write_cmd(0xF0, b"\x45\x09\x08\x08\x26\x2A")
        self._write_cmd(0xF1, b"\x43\x70\x72\x36\x37\x6F")
        self._write_cmd(0xF2, b"\x45\x09\x08\x08\x26\x2A")
        self._write_cmd(0xF3, b"\x43\x70\x72\x36\x37\x6F")
        self._write_cmd(0xED, b"\x1B\x0B")
        self._write_cmd(0xAE, b"\x77")
        self._write_cmd(0xCD, b"\x63")
        self._write_cmd(0x70, b"\x07\x07\x04\x0E\x0F\x09\x07\x08\x03")
        self._write_cmd(0xE8, b"\x34")
        self._write_cmd(0x62, b"\x18\x0D\x71\xED\x70\x70\x18\x0F\x71\xEF\x70\x70")
        self._write_cmd(0x63, b"\x18\x11\x71\xF1\x70\x70\x18\x13\x71\xF3\x70\x70")
        self._write_cmd(0x64, b"\x28\x29\xF1\x01\xF1\x00\x07")
        self._write_cmd(0x66, b"\x3C\x00\xCD\x67\x45\x45\x10\x00\x00\x00")
        self._write_cmd(0x67, b"\x00\x3C\x00\x00\x00\x01\x54\x10\x32\x98")
        self._write_cmd(0x74, b"\x10\x85\x80\x00\x00\x4E\x00")
        self._write_cmd(0x98, b"\x3E")
        self._write_cmd(0x99, b"\x3E")
        self._write_cmd(0x21)   # inversion ON (necessaire sur ce panel)
        self._write_cmd(0x11)   # sleep out
        time.sleep(0.12)
        self._write_cmd(0x29)   # display ON
        time.sleep(0.02)

    def _madctl_for_rotation(self):
        # Si l'image sort tournee ou en miroir, essaie une autre valeur
        # de ce dico (0, 90, 180 ou 270) via le parametre rotation=.
        table = {0: 0x48, 90: 0x28, 180: 0x88, 270: 0xE8}
        return table.get(self.rotation, 0x48)

    # -- API haut niveau : ce que le reste du script appelle ------------

    def _set_window(self, x0, y0, x1, y1):
        self._write_cmd(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._write_cmd(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self._write_cmd(0x2C)

    def image(self, img):
        """Affiche une image PIL - meme signature que l'ILI9341 utilise
        ailleurs dans ce projet."""
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))

        self._set_window(0, 0, self.width - 1, self.height - 1)

        if np is not None:
            arr = np.asarray(img, dtype=np.uint16)
            r = (arr[:, :, 0] & 0xF8) << 8
            g = (arr[:, :, 1] & 0xFC) << 3
            b = arr[:, :, 2] >> 3
            rgb565 = (r | g | b).astype(">u2")
            buf = rgb565.tobytes()
        else:
            pixels = img.load()
            buf = bytearray(self.width * self.height * 2)
            i = 0
            for y in range(self.height):
                for x in range(self.width):
                    rr, gg, bb = pixels[x, y]
                    val = ((rr & 0xF8) << 8) | ((gg & 0xFC) << 3) | (bb >> 3)
                    buf[i] = val >> 8
                    buf[i + 1] = val & 0xFF
                    i += 2
            buf = bytes(buf)

        self._write_pixels(buf)
