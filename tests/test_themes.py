import unittest

from desktop_buddy.themes import DEFAULT_THEME,ROLES,THEMES,contrast_ratio,theme_css

TEXT_SURFACES=('background','surface','sidebar','subtle','control','hover','selection','soft_hover','user')
MUTED_SURFACES=('background','surface','sidebar','control')
PAIRS=(('on_accent','accent'),('on_accent','accent_hover'),('on_danger','danger_fill'),
       ('error_text','error_bg'),('accent','background'),('accent','surface'),('accent','mark'),
       ('author','background'),('danger','background'))


class ThemeTests(unittest.TestCase):
    def test_contrast_ratio_matches_wcag_reference_values(self):
        self.assertAlmostEqual(contrast_ratio('#000000','#ffffff'),21.0,places=2)
        self.assertAlmostEqual(contrast_ratio('#ffffff','#000000'),21.0,places=2)
        self.assertAlmostEqual(contrast_ratio('#777777','#ffffff'),4.48,places=2)

    def test_every_theme_defines_every_role_with_a_hex_color(self):
        self.assertIn(DEFAULT_THEME,THEMES)
        for key,(label,palette) in THEMES.items():
            self.assertEqual(tuple(palette),ROLES,key)
            self.assertIn(' · ',label,key)
            self.assertTrue(label.endswith(('light','dark')),label)
            for role,value in palette.items():
                self.assertRegex(value,r'^#[0-9a-f]{6}$',f'{key}.{role}')

    def test_light_and_dark_themes_are_both_offered(self):
        labels=[label for label,_palette in THEMES.values()]
        self.assertGreaterEqual(sum(label.endswith('light') for label in labels),2)
        self.assertGreaterEqual(sum(label.endswith('dark') for label in labels),2)
        self.assertIn('High contrast · dark',labels)
        self.assertEqual(len(labels),len(set(labels)))

    def test_text_is_readable_on_every_surface_in_every_theme(self):
        for key,(_label,palette) in THEMES.items():
            for surface in TEXT_SURFACES:
                self.assertGreaterEqual(contrast_ratio(palette['text'],palette[surface]),4.5,f'{key}: text on {surface}')
            for surface in MUTED_SURFACES:
                self.assertGreaterEqual(contrast_ratio(palette['muted'],palette[surface]),4.5,f'{key}: muted on {surface}')
            for foreground,background in PAIRS:
                self.assertGreaterEqual(contrast_ratio(palette[foreground],palette[background]),4.5,f'{key}: {foreground} on {background}')

    def test_theme_css_defines_every_role_and_falls_back_to_default(self):
        for key,(_label,palette) in THEMES.items():
            css=theme_css(key)
            for role,value in palette.items():
                self.assertIn(f'@define-color buddy_{role} {value};',css)
        self.assertEqual(theme_css('no-such-theme'),theme_css(DEFAULT_THEME))


if __name__=='__main__':unittest.main()
