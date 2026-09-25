from PySide6.QtCore import QRect
import random

from codex_companion.app import popup_position


class Screen:
    def __init__(self, geometry: QRect, ratio: float):
        self._geometry = geometry
        self._ratio = ratio

    def geometry(self):
        return self._geometry

    def availableGeometry(self):
        return self._geometry

    def devicePixelRatio(self):
        return self._ratio


def test_physical_editor_coordinates_at_125_percent_stay_visible():
    screen = Screen(QRect(0, 0, 2048, 1152), 1.25)
    point = popup_position((400, 1250, 1200, 1380), 430, 83, [screen])
    assert point.x() == 320
    assert point.y() + 83 < 1000  # above the editor, never over its text


def test_second_monitor_uses_its_own_origin_and_scale():
    primary = Screen(QRect(0, 0, 2048, 1152), 1.25)
    second = Screen(QRect(2560, 0, 864, 1536), 1.25)
    point = popup_position((3000, 1300, 3400, 1400), 430, 83, [primary, second])
    assert 2560 <= point.x() <= 3424 - 430
    assert point.y() >= 1120  # below the editor


def test_uses_side_when_editor_reaches_top_and_bottom():
    screen = Screen(QRect(0, 0, 1200, 800), 1)
    editor = QRect(100, 0, 120, 800)
    point = popup_position((100, 0, 220, 800), 350, 83, [screen])
    popup = QRect(point.x(), point.y(), 350, 83)
    assert point.x() >= 228
    assert not popup.intersects(editor)


def test_hides_when_no_non_overlapping_position_exists():
    screen = Screen(QRect(0, 0, 800, 600), 1)
    assert popup_position((0, 0, 800, 600), 350, 83, [screen]) is None


def test_all_returned_positions_avoid_editor():
    screen = Screen(QRect(0, 0, 1200, 800), 1)
    rng = random.Random(42)
    area = screen.availableGeometry().adjusted(6, 6, -6, -6)
    for _ in range(300):
        left = rng.randrange(0, 1100)
        top = rng.randrange(0, 700)
        right = rng.randrange(left + 1, 1201)
        bottom = rng.randrange(top + 1, 801)
        point = popup_position((left, top, right, bottom), 350, 83, [screen])
        if point is not None:
            popup = QRect(point.x(), point.y(), 350, 83)
            assert area.contains(popup)
            assert not popup.intersects(QRect(left, top, right - left, bottom - top))
