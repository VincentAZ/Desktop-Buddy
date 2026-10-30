import unittest

from desktop_buddy.themes import theme_css
from desktop_buddy.ui_widgets import SIDEBAR_MIN, SIDEBAR_SHARE, SIDEBAR_WIDTH, sidebar_width


class SidebarWidthTest(unittest.TestCase):
    def test_a_dragged_width_is_kept(self):
        self.assertEqual(sidebar_width(420), 420)
        self.assertEqual(sidebar_width(420, 1600), 420)

    def test_never_narrower_than_the_buttons_need(self):
        self.assertEqual(sidebar_width(20), SIDEBAR_MIN)
        self.assertEqual(sidebar_width(-5, 1200), SIDEBAR_MIN)

    def test_leaves_room_for_the_conversation(self):
        self.assertEqual(sidebar_width(5000, 1000), int(1000 * SIDEBAR_SHARE))
        # A tiny window still gets the minimum rather than nothing.
        self.assertEqual(sidebar_width(5000, 100), SIDEBAR_MIN)

    def test_unreadable_saved_values_fall_back_to_the_default(self):
        for saved in (None, 'wide', [], {}):
            self.assertEqual(sidebar_width(saved), SIDEBAR_WIDTH)

    def test_divider_is_styled_in_every_theme(self):
        self.assertIn('paned.sidebar-split > separator', theme_css('sage'))


if __name__ == '__main__':
    unittest.main()
