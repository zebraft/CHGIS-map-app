import unittest

import pandas as pd

from CHGIS_map_app import (
    app,
    chgis_placename_url,
    combine_place_names,
    extract_place_names,
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

    def test_filter_data_empty_query_without_date_returns_no_rows(self):
        results = filter_data('', '', '', 'prefectures', 'counties')

        self.assertTrue(results.empty)

    def test_filter_data_empty_query_with_date_still_returns_date_rows(self):
        results = filter_data('', '1200', '', '', 'counties')

        self.assertFalse(results.empty)
        self.assertTrue(((results['BEG_YR'] <= 1200) & (results['END_YR'] >= 1200)).all())

    def test_filter_data_date_range_does_not_raise(self):
        results = filter_data('保德縣', '', '1100,1300', '', 'counties')

        self.assertFalse(results.empty)

    def test_extract_place_names_matches_simplified_to_traditional_canonical_name(self):
        results = extract_place_names('保德县在此。保德县又見。', '', 'counties')

        self.assertEqual(results[0]['name'], '保德縣')
        self.assertEqual(results[0]['variant_display'], '保德县')
        self.assertEqual(results[0]['count'], 2)

    def test_extract_place_names_prefers_long_place_names(self):
        results = extract_place_names('晉陽縣置於此。', '', 'counties')
        result_names = [result['name'] for result in results]

        self.assertIn('晉陽縣', result_names)

    def test_combine_place_names_deduplicates_manual_and_extracted_names(self):
        self.assertEqual(
            combine_place_names('太原，保德縣', ['保德縣', '晉陽縣']),
            '太原，保德縣，晉陽縣',
        )

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

    def test_marker_for_row_skips_rows_with_missing_coordinates(self):
        row = pd.Series({
            'NAME_FT': 'No Coordinates',
            'X_COOR': float('nan'),
            'Y_COOR': 37.87,
            'LEV_RANK': 6,
            'BEG_YR': 1,
            'END_YR': 2,
            'BEG_CHG_TY': '新建',
            'END_CHG_TY': '撤销',
        })

        self.assertIsNone(marker_for_row(row))

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
        self.assertIn('"zoomToBoundsOnClick": false', html)
        self.assertIn('"maxClusterRadius": 35', html)
        self.assertIn('"maxZoom": 13', html)
        self.assertIn('World_Shaded_Relief', html)
        self.assertIn(".on('clusterclick'", html)
        self.assertIn('event.layer.spiderfy();', html)

    def test_posted_source_text_renders_detected_place_list(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '保德县在此。保德县又見。',
                'date': '1200',
                'date_range': '',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Detected Place Names', html)
        self.assertIn('保德縣', html)
        self.assertIn('2 occurrences', html)

    def test_posted_unmatched_source_text_does_not_render_all_chgis_rows(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '長安洛陽。',
                'date': '',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.get_data()), 200000)


if __name__ == '__main__':
    unittest.main()
