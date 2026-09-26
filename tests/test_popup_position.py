from PySide6.QtCore import QRect
import random

import pytest

from codex_companion.app import SuggestionPopup, popup_position


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


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0])
def test_all_returned_positions_avoid_editor(ratio):
    screen = Screen(QRect(0, 0, 1200, 800), ratio)
    rng = random.Random(42)
    area = screen.availableGeometry().adjusted(6, 6, -6, -6)
    for _ in range(300):
        left = rng.randrange(0, 1100)
        top = rng.randrange(0, 700)
        right = rng.randrange(left + 1, 1201)
        bottom = rng.randrange(top + 1, 801)
        width, height = rng.choice((338, 480)), rng.choice((48, 77, 111, 180))
        bounds = tuple(round(p * ratio) for p in (left, top, right, bottom))
        point = popup_position(bounds, width, height, [screen])
        if point is not None:
            popup = QRect(point.x(), point.y(), width, height)
            assert area.contains(popup)
            assert not popup.intersects(QRect(left, top, right - left, bottom - top))


def physical_bounds(editor, screen):
    origin = screen.geometry().topLeft()
    return tuple(o + round((p - o) * screen.devicePixelRatio()) for p, o in zip(
        (editor.left(), editor.top(), editor.x() + editor.width(), editor.y() + editor.height()),
        (origin.x(), origin.y(), origin.x(), origin.y())))


@pytest.mark.parametrize("text", [
    "First line to inspect.\nSecond line to adjust.\nThird line to verify.",
    "请检查标题与正文的对齐，适当增加段落间距，让长文本在较窄的窗口中自然换行，确认所有内容都能完整显示。",
    "Please check the spacing between headings and paragraphs. Keep long text readable "
    "when the window gets narrower, and make sure the existing controls remain visible.",
])
@pytest.mark.parametrize("initial", ["Loading model...", "A short suggestion."])
def test_growing_popup_stays_clear_of_editor_throughout_animation(qapp, qtbot, initial, text):
    screen = qapp.primaryScreen()
    area = screen.availableGeometry()
    editor = QRect(area.left() + 80, area.bottom() - 55, min(650, area.width() - 160), 40)
    bounds = physical_bounds(editor, screen)
    popup = SuggestionPopup()
    qtbot.addWidget(popup)
    popup.show_text(initial, bounds, suggest=initial != "Loading model...")
    popup._appear.setCurrentTime(35)
    previous_height = popup.height()
    popup.show_text(text, bounds, suggest=True)
    assert popup.height() > previous_height
    assert popup.label.text() == text
    for _ in range(15):
        assert popup.isVisible()
        assert area.contains(popup.frameGeometry())
        assert popup.label.parentWidget().rect().contains(popup.label.geometry())
        assert popup.frameGeometry().bottom() < editor.top()
        assert not popup.frameGeometry().intersects(editor)
        qtbot.wait(10)


def test_moving_editor_during_appearance_does_not_restore_old_position(qapp, qtbot):
    screen = qapp.primaryScreen()
    area = screen.availableGeometry()
    editor = QRect(area.left() + 80, area.bottom() - 55, min(650, area.width() - 160), 40)
    popup = SuggestionPopup()
    qtbot.addWidget(popup)
    text = "First line.\nSecond line.\nThird line."
    popup.show_text(text, physical_bounds(editor, screen), suggest=True)
    popup._appear.setCurrentTime(35)
    editor.translate(0, -popup.height())
    popup.show_text(text, physical_bounds(editor, screen), suggest=True)
    placed = popup.pos()
    for _ in range(15):
        assert popup.pos() == placed
        assert not popup.frameGeometry().intersects(editor)
        qtbot.wait(10)
