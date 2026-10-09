"""tests/test_camara.py — Tests offline para quick_actions.capture_webcam.

NINGUN test abre la webcam real: se inyecta un modulo cv2 FALSO en
sys.modules ANTES de que capture_webcam haga su import interno, asi el
helper corre contra el fake sin hardware.
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quick_actions


class FakeCapture:
    """Imita cv2.VideoCapture para un indice de camara."""

    def __init__(self, opens, frames):
        self._opens = opens
        self._frames = list(frames)  # lista de tuples (ok, frame) en orden
        self.released = False
        self.set_calls = {}

    def isOpened(self):
        return self._opens

    def read(self):
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
    """Base: arma un cv2 fake con capturas por indice y lo inyecta."""

    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_DSHOW = 700  # valor cualquiera; el fake no valida

    def setUp(self):
        self._cv2_original = sys.modules.get("cv2")
        self._sleep_patcher = mock.patch("time.sleep")  # warm-up instantaneo
        self._sleep_patcher.start()
        self.imwrite_escritos = []

    def tearDown(self):
        self._sleep_patcher.stop()
        if self._cv2_original is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = self._cv2_original

    def _instalar_fake(self, capturas):
        """capturas: dict indice -> FakeCapture. Los indices ausentes se
        crean cerrados (isOpened False), como hace cv2 real."""
        capturas = dict(capturas)
        mod = types.ModuleType("cv2")
        mod.CAP_DSHOW = self.CAP_DSHOW
        mod.CAP_PROP_FRAME_WIDTH = self.CAP_PROP_FRAME_WIDTH
        mod.CAP_PROP_FRAME_HEIGHT = self.CAP_PROP_FRAME_HEIGHT

        def VideoCapture(idx, api=None):
            if idx not in capturas:
                capturas[idx] = FakeCapture(opens=False, frames=[])
            return capturas[idx]

        mod.VideoCapture = VideoCapture

        def imwrite(ruta, frame):
            Path(ruta).write_bytes(b"jpg-simulado")
            self.imwrite_escritos.append((ruta, frame))
            return True

        mod.imwrite = imwrite
        sys.modules["cv2"] = mod
        return mod


class TestCaptureWebcam(FakeCV2TestBase):

    def test_a_captura_feliz(self):
        """(a) captura feliz -> archivo existe y release() fue llamado."""
        frame_ok = ("frame", object())
        cap0 = FakeCapture(opens=True, frames=[(True, object())] * 7 + [frame_ok])
        self._instalar_fake({0: cap0})

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(ruta.endswith(".jpg"))
        self.assertTrue(cap0.released)
        # warm-up: se pidio resolucion 1280x720
        self.assertEqual(
            cap0.set_calls.get(self.CAP_PROP_FRAME_WIDTH), 1280)
        self.assertEqual(
            cap0.set_calls.get(self.CAP_PROP_FRAME_HEIGHT), 720)

    def test_b_camara_0_cerrada_camara_1_abre(self):
        """(b) indice 0 no abre pero indice 1 si -> funciona."""
        cap1 = FakeCapture(
            opens=True, frames=[(True, object())] * 8)
        self._instalar_fake({1: cap1})

        ruta = quick_actions.capture_webcam()

        self.assertTrue(Path(ruta).exists())
        self.assertTrue(cap1.released)

    def test_c_ninguna_camara(self):
        """(c) ninguna camara abre -> RuntimeError 'no se encontró'."""
        self._instalar_fake({})

        with self.assertRaises(RuntimeError) as ctx:
            quick_actions.capture_webcam()
        self.assertIn("no se encontró", str(ctx.exception))

    def test_d_frame_final_vacio(self):
        """(d) frame final vacio -> RuntimeError menciona 'frame'."""
        cap0 = FakeCapture(
            opens=True, frames=[(True, object())] * 7 + [(False, None)])
        self._instalar_fake({0: cap0})

        with self.assertRaises(RuntimeError) as ctx:
            quick_actions.capture_webcam()
        self.assertIn("frame", str(ctx.exception))
        # ante el error igual se libera la camara
        self.assertTrue(cap0.released)

    def test_e_ruta_explicita_se_respeta(self):
        """(extra) si pasan ruta, escribe ahi; release ante fallo de captura."""
        cap0 = FakeCapture(opens=False, frames=[])
        self._instalar_fake({0: cap0})
        with tempfile.TemporaryDirectory() as tmp:
            destino = str(Path(tmp) / "foto.jpg")
            with self.assertRaises(RuntimeError):
                quick_actions.capture_webcam(destino)
        self.assertTrue(cap0.released)


if __name__ == "__main__":
    unittest.main()
