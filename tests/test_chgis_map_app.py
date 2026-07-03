import unittest

import pandas as pd

from CHGIS_map_app import (
    alias_only_match,
    app,
    chgis_placename_url,
    combine_place_names,
    extract_place_names,
    filter_data,
    generate_map,
    highlighted_source_text,
    marker_for_row,
    followed_by_reign_year,
    short_alias,
)


class ChgisMapAppTest(unittest.TestCase):
    def test_chgis_placename_url_encodes_place_name(self):
        self.assertEqual(
            chgis_placename_url('晋阳'),
            'https://chgis.hudci.org/tgaz/placename?n=%E6%99%8B%E9%98%B3',
        )

    def test_filter_data_matches_county_by_name_and_date(self):
        results = filter_data('保德縣', (1200, 1200), '', 'counties')

        self.assertFalse(results.empty)
        self.assertTrue((results['NAME_FT'] == '保德縣').any())
        self.assertTrue(((results['BEG_YR'] <= 1200) & (results['END_YR'] >= 1200)).all())

    def test_filter_data_empty_query_without_date_returns_no_rows(self):
        results = filter_data('', None, 'prefectures', 'counties')

        self.assertTrue(results.empty)

    def test_filter_data_empty_query_with_date_still_returns_date_rows(self):
        results = filter_data('', (1200, 1200), '', 'counties')

        self.assertFalse(results.empty)
        self.assertTrue(((results['BEG_YR'] <= 1200) & (results['END_YR'] >= 1200)).all())

    def test_filter_data_empty_query_can_disable_date_only_fallback(self):
        results = filter_data('', (1200, 1200), '', 'counties', allow_date_only=False)

        self.assertTrue(results.empty)

    def test_filter_data_date_range_does_not_raise(self):
        results = filter_data('保德縣', (1100, 1300), '', 'counties')

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

    def test_short_alias_strips_common_admin_suffixes(self):
        self.assertEqual(short_alias('東莞郡'), '東莞')
        self.assertEqual(short_alias('東莞縣'), '東莞')

    def test_extract_place_names_matches_short_admin_aliases(self):
        text = '東莞劉穆之，字道和，小字道人。世居京口。'
        results = extract_place_names(text, 'prefectures', 'counties')
        result_names = {result['name'] for result in results}

        self.assertIn('東莞郡', result_names)
        self.assertIn('東莞縣', result_names)
        self.assertTrue(any(result['matched_by_alias'] for result in results if result['name'].startswith('東莞')))
        self.assertTrue(any(alias_only_match(result) for result in results if result['name'].startswith('東莞')))

    def test_reign_year_context_suppresses_alias_match(self):
        self.assertTrue(followed_by_reign_year('五年，王崩。', 0))
        results = extract_place_names('隆安五年，王崩。', 'prefectures', 'counties')
        result_names = {result['name'] for result in results}

        self.assertNotIn('隆安縣', result_names)

    def test_highlighted_source_text_marks_detected_variants(self):
        text = '東莞劉穆之，字道和，小字道人。'
        matches = extract_place_names(text, 'prefectures', 'counties')
        html = str(highlighted_source_text(text, matches))

        self.assertIn("class='source-place'", html)
        self.assertIn("data-place-names='東莞侯國|東莞縣|東莞郡'", html)
        self.assertIn('>東莞</mark>', html)

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

    def test_generate_map_groups_overlapping_points_in_one_popup(self):
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

        self.assertIn('2 CHGIS records at this location', html)
        self.assertIn('<table', html)
        self.assertIn('min-width:420px', html)
        self.assertIn('晋阳', html)
        self.assertIn('晋阳县', html)
        self.assertIn('World_Shaded_Relief', html)
        self.assertIn('"maxZoom": 13', html)
        self.assertIn('"zoomToBoundsOnClick": false', html)
        self.assertIn('World_Shaded_Relief', html)

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
        self.assertIn('<h2>Passage</h2>', html)
        self.assertIn("class='source-place'", html)
        self.assertIn('place-toggle', html)
        self.assertIn('scheduleMapUpdate', html)
        self.assertIn('form.submit()', html)
        self.assertIn('保德縣', html)
        self.assertIn('2 occurrences', html)
        self.assertIn('1 date-matched record', html)
        self.assertIn('on map', html)

    def test_date_filtered_out_detected_place_is_unchecked(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '保德县在此。',
                'date': '1300',
                'date_range': '',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('保德縣', html)
        self.assertNotIn("class='source-place'", html)
        self.assertIn('No place names were detected from pasted text.', html)

    def test_alias_candidate_list_is_filtered_by_date_before_rendering(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '東莞劉穆之，字道和。',
                'date': '700',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('東莞郡', html)
        self.assertNotIn('東莞縣', html)
        self.assertNotIn("class='source-place'", html)

    def test_posted_source_text_can_deselect_all_detected_places(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '保德县在此。',
                'date': '',
                'date_range': '',
                'counties': 'counties',
                'detected_selection_submitted': '1',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('保德縣', html)
        self.assertIn('2 date-matched records', html)
        self.assertIn('not on map', html)

    def test_posted_alias_matches_are_mapped_by_default(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '東莞劉穆之，字道和，小字道人。世居京口。',
                'date': '',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('東莞郡', html)
        self.assertIn('alias match 東莞', html)
        self.assertIn('on map', html)
        self.assertIn('value="東莞郡" checked', html)

    def test_jiangzhou_426_maps_date_valid_alias_candidate(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '江州刺史',
                'date': '426',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('江州縣', html)
        self.assertIn('alias match 江州', html)
        self.assertIn('1 date-matched record', html)
        self.assertIn('value="江州縣" checked', html)
        self.assertIn('on map', html)

    def test_invalid_date_renders_search_error(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '保德縣',
                'source_text': '',
                'date': 'twelve hundred',
                'date_range': '',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Date must be a whole year', html)
        self.assertIn('twelve hundred', html)

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

    def test_posted_unmatched_source_text_with_date_range_does_not_become_date_only_search(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '長安洛陽。',
                'date': '',
                'date_range': '700,1000',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('showing the first 1000', html)
        self.assertLess(len(response.get_data()), 200000)

    def test_date_only_large_results_show_warning_and_are_capped(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '',
                'date': '1200',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('showing the first 1000', html)


if __name__ == '__main__':
    unittest.main()
