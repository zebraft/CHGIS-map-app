import unittest

import pandas as pd

from CHGIS_map_app import (
    chgis_placename_url,
    filter_data,
    generate_map,
    marker_for_row,
)


class ChgisMapAppTest(unittest.TestCase):
    def test_chgis_placename_url_encodes_place_name(self):
        self.assertEqual(
            chgis_placename_url('晋阳'),
            'https://chgis.hudci.org/tgaz/placename?n=%E6%99%8B%E9%98%B3',
        )

    def test_filter_data_matches_county_by_name_and_date(self):
        results = filter_data('保德縣', '1200', '', '', 'counties')

        self.assertFalse(results.empty)
        self.assertTrue((results['NAME_FT'] == '保德縣').any())
        self.assertTrue(((results['BEG_YR'] <= 1200) & (results['END_YR'] >= 1200)).all())

    def test_marker_for_row_uses_encoded_chgis_popup(self):
        row = pd.Series({
            'NAME_FT': '晋阳',
            'X_COOR': 112.55,
            'Y_COOR': 37.87,
            'LEV_RANK': 3,
            'BEG_YR': 1,
            'END_YR': 2,
            'BEG_CHG_TY': '新建',
            'END_CHG_TY': '撤销',
        })

        marker = marker_for_row(row)
        popup = next(child for child in marker._children.values() if child._name == 'Popup')
        html = popup.html.render()

        self.assertIn('https://chgis.hudci.org/tgaz/placename?n=%E6%99%8B%E9%98%B3', html)

    def test_generate_map_keeps_overlapping_points_clustered_until_deep_zoom(self):
        rows = pd.DataFrame([
            {
                'NAME_FT': '晋阳',
                'X_COOR': 112.55,
                'Y_COOR': 37.87,
                'LEV_RANK': 3,
                'BEG_YR': 1,
                'END_YR': 2,
                'BEG_CHG_TY': '新建',
                'END_CHG_TY': '撤销',
            },
            {
                'NAME_FT': '晋阳县',
                'X_COOR': 112.55,
                'Y_COOR': 37.87,
                'LEV_RANK': 6,
                'BEG_YR': 1,
                'END_YR': 2,
                'BEG_CHG_TY': '新建',
                'END_CHG_TY': '撤销',
            },
        ])

        html = generate_map(rows).get_root().render()

        self.assertIn('"disableClusteringAtZoom": 13', html)
        self.assertIn('"spiderfyOnMaxZoom": true', html)
        self.assertIn('"maxClusterRadius": 35', html)


if __name__ == '__main__':
    unittest.main()
