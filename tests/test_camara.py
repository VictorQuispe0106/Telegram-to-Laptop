"""tests/test_camara.py — Tests offline para quick_actions.capture_webcam.

NINGUN test abre la webcam real: se inyecta un modulo cv2 FALSO en
sys.modules ANTES de que capture_webcam haga su import interno, asi el
helper corre contra el fake sin hardware.

El helper actual prueba combinaciones (backend, indice) — CAP_DSHOW y
CAP_MSMF sobre los indices 0/1/2 — y devuelve el PRIMER frame valido de
hasta 30 lecturas con pausa, por eso el fake registra los pedidos.
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quick_actions


def frame_falso():
    """Imita un frame de OpenCV: lo unico que el helper valida es .shape."""
    return types.SimpleNamespace(shape=(480, 640, 3))


class FakeCapture:
    """Imita cv2.VideoCapture para una combinacion (backend, indice)."""

    def __init__(self, opens, frames):
        self._opens = opens
        self._frames = list(frames)  # lista de tuples (ok, frame) en orden
        self.released = False
        self.set_calls = {}
        self.lecturas = 0

    def isOpened(self):
        return self._opens

    def read(self):
        self.lecturas += 1
        if self._frames:
            return self._frames.pop(0)
        return (False, None)

    def set(self, prop, value):
        self.set_calls[prop] = value
        return True

    def get(self, prop):
        return self.set_calls.get(prop, 0)

    def release(self):
        self.released = True


class FakeCV2TestBase(unittest.TestCase):
    """Base: arma un cv2 fake con capturas por (backend, indice) y lo inyecta."""

    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_DSHOW = 700  # valores cualquiera; el fake no valida
    CAP_MSMF = 800

    def setUp(self):
        self._cv2_original = sys.modules.get("cv2")
        self._sleep_patcher = mock.patch("quick_actions.time.sleep")  # pausas instantaneas
        self._sleep_patcher.start()
        self.imwrite_escritos = []
        self.pedidos = []  # lista de (backend, idx) pedidos a VideoCapture

    def tearDown(self):
        self._sleep_patcher.stop()
        if self._cv2_original is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = self._cv2_original

    def _instalar_fake(self, capturas):
        """capturas: dict (backend, idx) -> FakeCapture. Toda combinacion
        ausente se crea cerrada (isOpened False), como hace cv2 real."""
        capturas = dict(capturas)
        mod = types.ModuleType("cv2")
        mod.CAP_DSHOW = self.CAP_DSHOW
        mod.CAP_MSMF = self.CAP_MSMF
        mod.CAP_PROP_FRAME_WIDTH = self.CAP_PROP_FRAME_WIDTH
        mod.CAP_PROP_FRAME_HEIGHT = self.CAP_PROP_FRAME_HEIGHT

        def VideoCapture(idx, api=None):
            self.pedidos.append((api, idx))
            clave = (api, idx)
            if clave not in capturas:
                capturas[clave] = FakeCapture(opens=False, frames=[])
            return capturas[clave]

        mod.VideoCapture = VideoCapture

        def imwrite(ruta, frame):
            Path(ruta).write_bytes(b"jpg-simulado")
            self.imwrite_escritos.append((ruta, frame))
            return True

        mod.imwrite = imwrite
        sys.modules["cv2"] = mod
        return mod, capturas


class TestCaptureWebcam(FakeCV2TestBase):

    def test_a_captura_feliz(self):
        """(a) captura feliz -> primero frame valido gana, release() y set."""
        frame_ok = frame_falso()
        # el cap arranca tirando frames vacios; el PRIMERO valido debe ganar
        cap0 = FakeCapture(opens=True, frames=[(False, None)] * 5 + [(True, frame_ok)])
        mod, caps = self._instalar_fake({(self.CAP_DSHOW, 0): cap0})

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(ruta.endswith(".jpg"))
        self.assertTrue(cap0.released)
        # escribio el PRIMER frame valido
        self.assertIs(self.imwrite_escritos[0][1], frame_ok)
        self.assertEqual(cap0.lecturas, 6)  # 5 vacios + 1 valido
        # warm-up pedido: resolucion 1280x720
        self.assertEqual(cap0.set_calls.get(self.CAP_PROP_FRAME_WIDTH), 1280)
        self.assertEqual(cap0.set_calls.get(self.CAP_PROP_FRAME_HEIGHT), 720)

    def test_b_indice_0_no_abre_indice_1_abre(self):
        """(b) DSHOW idx 0 no abre pero DSHOW idx 1 si -> funciona."""
        cap1 = FakeCapture(opens=True, frames=[(True, frame_falso())])
        mod, caps = self._instalar_fake({(self.CAP_DSHOW, 1): cap1})

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(cap1.released)
        # nunca pido DSHOW idx 2 ni MSMF: el frame ya llego
        self.assertEqual(
            self.pedidos, [(self.CAP_DSHOW, 0), (self.CAP_DSHOW, 1)])

    def test_c_ninguna_camara(self):
        """(c) ninguna camara abre -> RuntimeError 'no se encontró'."""
        mod, caps = self._instalar_fake({})

        with self.assertRaises(RuntimeError) as ctx:
            quick_actions.capture_webcam()
        self.assertIn("no se encontró", str(ctx.exception))

    def test_d_frame_nunca_valido(self):
        """(d) el cap abre pero nunca da frame -> RuntimeError 'no devolvió frames'."""
        cap0 = FakeCapture(opens=True, frames=[])  # read -> (False, None) siempre
        mod, caps = self._instalar_fake({(self.CAP_DSHOW, 0): cap0})

        with self.assertRaises(RuntimeError) as ctx:
            quick_actions.capture_webcam()
        self.assertIn("no devolvió frames", str(ctx.exception))
        # agoto los 30 intentos de warm-up y libero igual
        self.assertEqual(cap0.lecturas, 30)
        self.assertTrue(cap0.released)

    def test_e_fallback_msmf(self):
        """(e) DSHOW falla en todos los indices pero MSMF idx 0 abre y da frame."""
        cap_dshow0 = FakeCapture(opens=True, frames=[])  # abre pero no da frames
        cap_msmf0 = FakeCapture(opens=True, frames=[(True, frame_falso())])
        mod, caps = self._instalar_fake({
            (self.CAP_DSHOW, 0): cap_dshow0,
            (self.CAP_MSMF, 0): cap_msmf0,
        })

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(cap_dshow0.released)
        self.assertTrue(cap_msmf0.released)
        self.assertEqual(
            self.pedidos,
            [(self.CAP_DSHOW, 0), (self.CAP_DSHOW, 1), (self.CAP_DSHOW, 2),
             (self.CAP_MSMF, 0)])

    def test_f_ruta_explicita_se_respeta(self):
        """(extra) si pasan ruta, escribe ahi; release ante fallo de apertura."""
        cap0 = FakeCapture(opens=True, frames=[(True, frame_falso())])
        mod, caps = self._instalar_fake({(self.CAP_DSHOW, 0): cap0})
        with tempfile.TemporaryDirectory() as tmp:
            destino = str(Path(tmp) / "foto.jpg")
            ruta = quick_actions.capture_webcam(destino)
            self.assertEqual(ruta, destino)
            self.assertTrue(Path(destino).exists())
        self.assertTrue(cap0.released)

    def test_g_cap_cerrada_libera_igual(self):
        """(extra) una cap que abre False tambien se libera antes de la proxima."""
        cap_cerrada = FakeCapture(opens=False, frames=[])
        cap1 = FakeCapture(opens=True, frames=[(True, frame_falso())])
        mod, caps = self._instalar_fake({
            (self.CAP_DSHOW, 0): cap_cerrada,
            (self.CAP_DSHOW, 1): cap1,
        })

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(cap_cerrada.released)
        self.assertTrue(cap1.released)


if __name__ == "__main__":
    unittest.main()
