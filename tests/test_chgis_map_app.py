import unittest
import tempfile
from pathlib import Path

import pandas as pd

import CHGIS_map_app as chgis_module
from CHGIS_map_app import (
    alias_only_match,
    ai_cleaned_place_matches,
    ai_candidate_places,
    app,
    apply_detected_record_selection,
    chgis_placename_url,
    combine_place_names,
    extract_reign_dates,
    extract_place_names,
    filter_data,
    generate_map,
    highlighted_source_text,
    inferred_date_filter,
    label_html_for_group,
    map_bounds_for_locations,
    marker_for_row,
    followed_by_reign_year,
    short_alias,
    tag_choronym_rows,
)


class ChgisMapAppTest(unittest.TestCase):
    def setUp(self):
        self.original_ai_judge = chgis_module.judge_place_candidates_with_ai
        chgis_module.judge_place_candidates_with_ai = lambda _source_text, _candidates: []

    def tearDown(self):
        chgis_module.judge_place_candidates_with_ai = self.original_ai_judge

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

    def test_filter_data_matches_direct_short_alias(self):
        results = filter_data('五原', None, 'prefectures', 'counties')
        result_names = set(results['NAME_FT'])

        self.assertIn('五原郡', result_names)
        self.assertIn('五原縣', result_names)
        self.assertIn('五原廳', result_names)

    def test_filter_data_matches_direct_simplified_name(self):
        results = filter_data('五原县', None, '', 'counties')

        self.assertFalse(results.empty)
        self.assertIn('五原縣', set(results['NAME_FT']))

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

    def test_detected_record_selection_can_keep_one_duplicate_name_record(self):
        results = filter_data('保德縣', None, '', 'counties')
        record_ids = list(results['RECORD_ID'].astype(str))

        selected = apply_detected_record_selection(
            results,
            results,
            {record_ids[0]},
            [],
        )

        self.assertEqual(set(selected['RECORD_ID'].astype(str)), {record_ids[0]})

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

    def test_extract_place_names_returns_text_order_not_frequency_order(self):
        results = extract_place_names('保德县在前。東莞又見東莞。', 'prefectures', 'counties')
        result_names = [result['name'] for result in results]

        self.assertLess(result_names.index('保德縣'), result_names.index('東莞郡'))

    def test_reign_year_context_suppresses_alias_match(self):
        self.assertTrue(followed_by_reign_year('五年，王崩。', 0))
        results = extract_place_names('隆安五年，王崩。', 'prefectures', 'counties')
        result_names = {result['name'] for result in results}

        self.assertNotIn('隆安縣', result_names)

    def test_extract_reign_dates_matches_cbdb_reign_title(self):
        results = extract_reign_dates('隆安中，鳳凰集其庭。')

        self.assertTrue(results)
        self.assertEqual(results[0]['title'], '隆安')
        self.assertEqual(results[0]['matched_text'], '隆安中')
        self.assertIn('東晉', results[0]['date_display'])
        self.assertIn('397-401', results[0]['date_display'])

    def test_extract_reign_dates_requires_date_suffix(self):
        bare_results = extract_reign_dates('至太建末，寶應破，至德初。')
        matched_texts = [result['matched_text'] for result in bare_results]

        self.assertIn('太建末', matched_texts)
        self.assertIn('至德初', matched_texts)
        self.assertNotIn('寶應', matched_texts)

    def test_inferred_date_filter_uses_detected_reign_dates(self):
        self.assertEqual(
            inferred_date_filter(extract_reign_dates('天嘉中，荔卒。')),
            (560, 566),
        )

    def test_inferred_date_filter_chooses_coherent_ambiguous_reign_periods(self):
        dates = extract_reign_dates('天嘉中，至太建末，至德初，大業初。')

        self.assertEqual(inferred_date_filter(dates), (560, 617))

    def test_reign_date_range_suppresses_place_match(self):
        date_matches = extract_reign_dates('隆安中，鳳凰集其庭。')
        place_matches = extract_place_names(
            '隆安中，鳳凰集其庭。',
            '',
            'counties',
            excluded_ranges=[(match['start'], match['end']) for match in date_matches],
        )
        result_names = {result['name'] for result in place_matches}

        self.assertNotIn('隆安縣', result_names)

    def test_highlighted_source_text_marks_detected_variants(self):
        text = '東莞劉穆之，字道和，小字道人。'
        matches = extract_place_names(text, 'prefectures', 'counties')
        html = str(highlighted_source_text(text, matches))

        self.assertIn("class='source-place'", html)
        self.assertIn("data-place-names='", html)
        self.assertIn("東莞侯國", html)
        self.assertIn("東莞縣", html)
        self.assertIn("東莞郡", html)
        self.assertIn("tabindex='0'", html)
        self.assertIn("data-primary-place='", html)
        self.assertIn('>東莞</mark>', html)

    def test_highlighted_source_text_marks_detected_reign_dates(self):
        text = '隆安中，鳳凰集其庭。'
        dates = extract_reign_dates(text)
        html = str(highlighted_source_text(text, [], dates))

        self.assertIn("class='source-date'", html)
        self.assertIn("data-date-index='1'", html)
        self.assertIn('>隆安中</mark>', html)

    def test_contextual_marker_marks_fief_title(self):
        text = '召為建安王法曹參軍。'
        places = extract_place_names(text, 'prefectures', 'counties')
        markers = chgis_module.extract_contextual_markers(text, places)
        html = str(highlighted_source_text(text, places, [], [], markers))

        self.assertTrue(markers)
        self.assertIn('建安王', [marker['text'] for marker in markers])
        self.assertIn("class='source-fief-title'", html)
        self.assertIn('>建安王</mark>', html)

    def test_fief_title_cleanup_marks_shixing_wang_without_place_highlight(self):
        text = '梁始興王在坐。'
        places = extract_place_names(text, 'prefectures', 'counties')
        markers = chgis_module.extract_contextual_markers(text, places)
        cleaned_places, cleanup = ai_cleaned_place_matches(text, places, [], [], markers)
        html = str(highlighted_source_text(text, cleaned_places, [], [], markers))

        self.assertIn('始興王', [marker['text'] for marker in markers])
        self.assertNotIn('始興縣', {place['name'] for place in cleaned_places})
        self.assertTrue(cleanup['removed'])
        self.assertIn("class='source-fief-title'", html)
        self.assertNotIn("class='source-place'", html)

    def test_fief_title_location_is_not_mapped_when_contextual_locations_unchecked(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '梁始興王在坐。',
                'date': '500',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("class='source-fief-title'", html)
        self.assertIn('No place names were detected from pasted text.', html)
        self.assertNotIn('Include fief-title and choronym locations', html)
        self.assertNotIn("document.querySelectorAll('.context-toggle input')", html)
        self.assertNotIn('"markerColor": "orange"', html)

    def test_ai_candidate_places_includes_contexts(self):
        text = '召為建安王法曹參軍。晉王令使者追之。'
        matches = [
            {'name': '王法縣', 'variants': ['王法'], 'alias_variants': ['王法']},
            {'name': '王令縣', 'variants': ['王令'], 'alias_variants': ['王令']},
        ]
        candidates = ai_candidate_places(text, matches)

        self.assertEqual([candidate['phrase'] for candidate in candidates], ['王法', '王令'])
        self.assertIn('建安王法曹', candidates[0]['contexts'][0])
        self.assertIn('晉王令使者', candidates[1]['contexts'][0])

    def test_ai_cleanup_removes_rejected_place_variants(self):
        text = '召為建安王法曹參軍。晉王令使者追之。'
        matches = [
            {
                'name': '王法縣',
                'variants': ['王法'],
                'alias_variants': ['王法'],
                'variant_display': '王法',
                'alias_variant_display': '王法',
                'matched_by_alias': True,
                'count': 1,
                'first_start': 4,
            },
            {
                'name': '王令縣',
                'variants': ['王令'],
                'alias_variants': ['王令'],
                'variant_display': '王令',
                'alias_variant_display': '王令',
                'matched_by_alias': True,
                'count': 1,
                'first_start': 14,
            },
        ]

        chgis_module.judge_place_candidates_with_ai = lambda _source_text, _candidates: [
            {'phrase': '王法', 'is_place': 'false', 'reason': 'part of 建安王法曹參軍'},
            {'phrase': '王令', 'is_place': False, 'reason': '王 is title and 令 is verb'},
        ]
        cleaned, cleanup = ai_cleaned_place_matches(text, matches)

        self.assertEqual(cleaned, [])
        self.assertTrue(cleanup['ran'])
        self.assertEqual([item['phrase'] for item in cleanup['removed']], ['王法', '王令'])
        self.assertIn('princely title', cleanup['removed'][0]['reason'])

    def test_ai_cleanup_receives_deterministic_candidates_before_reign_cleanup(self):
        text = '隆安中，鳳凰集其庭。'
        matched_dates = extract_reign_dates(text)
        matches = extract_place_names(text, '', 'counties')
        captured_candidates = []

        def capture_judge(_source_text, candidates):
            captured_candidates.extend(candidates)
            return []

        chgis_module.judge_place_candidates_with_ai = capture_judge
        cleaned, cleanup = ai_cleaned_place_matches(text, matches, matched_dates, [])

        self.assertIn('隆安', [candidate['phrase'] for candidate in captured_candidates])
        self.assertNotIn('隆安縣', {place['name'] for place in cleaned})
        self.assertTrue(cleanup['removed'])
        self.assertIn('reign name/date', cleanup['removed'][0]['reason'])

    def test_ai_place_cache_stores_empty_judgments_for_repeat_queries(self):
        original_cache_path = chgis_module.AI_PLACE_CACHE_PATH
        candidates = [{'phrase': '王法', 'candidate_names': ['王法縣'], 'contexts': ['建安王法曹']}]

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                chgis_module.AI_PLACE_CACHE_PATH = Path(tmpdir) / 'ai_place_cleanup_cache.json'
                chgis_module.store_ai_place_judgments('召為建安王法曹參軍。', candidates, [])

                cached = chgis_module.cached_ai_place_judgments('召為建安王法曹參軍。', candidates)
        finally:
            chgis_module.AI_PLACE_CACHE_PATH = original_cache_path
        self.assertEqual(cached, [])

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

    def test_marker_for_choronym_row_uses_distinct_map_pin(self):
        row = pd.Series({
            'NAME_FT': '保德縣',
            'X_COOR': 111.08,
            'Y_COOR': 39.01,
            'LEV_RANK': 6,
            'BEG_YR': 1171,
            'END_YR': 1256,
            'BEG_CHG_TY': '新建',
            'END_CHG_TY': '撤销',
            'MATCH_SOURCE': chgis_module.MATCH_SOURCE_CHORONYM,
        })

        html = generate_map(pd.DataFrame([row])).get_root().render()

        self.assertIn('"markerColor": "orange"', html)
        self.assertIn('"icon": "map-marker"', html)

    def test_tag_choronym_rows_marks_detected_place_records(self):
        rows = pd.DataFrame([
            {'NAME_FT': '保德縣', 'RECORD_ID': 'a'},
            {'NAME_FT': '晉陽縣', 'RECORD_ID': 'b'},
        ])

        tagged = tag_choronym_rows(rows, [{'name': '保德縣'}])

        self.assertEqual(
            tagged.loc[tagged['NAME_FT'] == '保德縣', 'MATCH_SOURCE'].iloc[0],
            chgis_module.MATCH_SOURCE_CHORONYM,
        )
        self.assertEqual(tagged.loc[tagged['NAME_FT'] == '晉陽縣', 'MATCH_SOURCE'].iloc[0], '')

    def test_map_bounds_expand_single_point(self):
        self.assertEqual(
            map_bounds_for_locations([(37.87, 112.55)]),
            [[37.12, 111.8], [38.62, 113.3]],
        )

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
        self.assertIn('CHGIS', html)
        self.assertIn('World_Shaded_Relief', html)
        self.assertIn('Shaded relief', html)
        self.assertIn('World_Topo_Map', html)
        self.assertIn('plain base - CHGIS 1820', html)
        self.assertIn('/CHGIS_plain_base.geojson', html)
        self.assertIn('chgisPlainBasePane', html)
        self.assertIn('chgisHistoricalOverlayPane', html)
        self.assertIn("fillColor: '#d7e7ea'", html)
        self.assertIn("fillColor: '#f8f4e8'", html)
        self.assertIn('"opacity": 1', html)
        self.assertIn('layer_control', html)
        self.assertIn('fitBounds', html)
        self.assertIn('[37.12, 111.8]', html)
        self.assertIn('[38.62, 113.3]', html)
        self.assertIn('"maxZoom": 13', html)
        self.assertIn('"zoomToBoundsOnClick": false', html)
        self.assertIn('Neutral topo map', html)

    def test_chgis_plain_base_endpoint_returns_local_vector_layers(self):
        with app.test_client() as client:
            response = client.get('/CHGIS_plain_base.geojson')

        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn('province_boundaries', data)
        self.assertIn('lakes', data)
        self.assertIn('rivers', data)
        self.assertTrue(data['province_boundaries'])
        self.assertTrue(data['lakes'])
        self.assertTrue(data['rivers'])
        self.assertEqual(data['province_boundaries'][0]['type'], 'Feature')

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
        self.assertIn('Place Candidates', html)
        self.assertIn('<h2>Passage</h2>', html)
        self.assertIn("class='source-place'", html)
        self.assertIn('record-toggle', html)
        self.assertIn('name="selected_records"', html)
        self.assertIn('window.CHGISMapSelection.apply', html)
        self.assertIn('event.preventDefault()', html)
        self.assertNotIn('scheduleMapUpdate', html)
        self.assertNotIn('form.submit()', html)
        self.assertNotIn('requestSubmit', html)
        self.assertIn('locatePlaceFromText', html)
        self.assertIn('data-place-name="保德縣"', html)
        self.assertIn('保德縣', html)
        self.assertIn('2 occurrences', html)
        self.assertIn('on map', html)
        self.assertIn('县 1171-1256', html)

    def test_posted_map_uses_place_labels_by_default(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '保德縣',
                'source_text': '',
                'date': '1200',
                'date_range': '',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('historical-place-label-icon', html)
        self.assertIn('chgisHistoricalOverlayPane', html)
        self.assertIn('保德縣', html)
        self.assertNotIn('markerClusterGroup', html)
        self.assertIn('fitBounds', html)
        self.assertIn('"maxZoom": 7', html)
        self.assertIn('"padding": [95, 95]', html)

    def test_place_label_html_omits_administrative_category_text(self):
        rows = [
            pd.Series({
                'NAME_FT': '保德縣',
                'TYPE_CH': '县',
                'LEV_RANK': 6,
                'BEG_YR': 1171,
                'END_YR': 1256,
            })
        ]

        html = label_html_for_group(rows)

        self.assertIn('保德縣', html)
        self.assertNotIn('>县</span>', html)

    def test_posted_map_can_use_clustered_pins_when_label_mode_unchecked(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '保德縣',
                'source_text': '',
                'date': '1200',
                'date_range': '',
                'counties': 'counties',
                'style_options_submitted': '1',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('markerClusterGroup', html)
        self.assertNotIn('historical-place-label-icon', html)

    def test_posted_source_text_renders_detected_dates(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '隆安中，鳳凰集其庭。',
                'date': '',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Dates', html)
        self.assertIn('date-timeline', html)
        self.assertIn('timeline-date', html)
        self.assertIn('timeline-span', html)
        self.assertIn('timeline-years', html)
        self.assertIn("class='source-date'", html)
        self.assertIn('隆安中', html)
        self.assertIn('397-401', html)
        self.assertIn('No place names were detected from pasted text.', html)
        self.assertNotIn('class="matched-place"', html)

    def test_cleanup_notice_describes_people_titles_or_reign_names(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '隆安中，鳳凰集其庭。',
                'date': '',
                'date_range': '',
                'prefectures': '',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Reading Cleanup', html)
        self.assertIn('Removed matches used as people, titles, reign names, or other non-place wording:', html)
        self.assertNotIn('removed likely false place matches', html)

    def test_detected_reign_date_limits_place_records_when_date_blank(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '天嘉中，越州餘姚人。',
                'date': '',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('餘姚縣', html)
        self.assertIn('-202-588', html)
        self.assertNotIn('621-1294', html)

    def test_explicit_date_overrides_detected_reign_date_filter(self):
        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '天嘉中，越州餘姚人。',
                'date': '700',
                'date_range': '',
                'prefectures': 'prefectures',
                'counties': 'counties',
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('餘姚縣', html)
        self.assertIn('621-1294', html)
        self.assertNotIn('-202-588', html)

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
        self.assertIn('not on map', html)
        self.assertNotIn('checked>', html)

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
        self.assertIn('data-place-name="東莞郡"', html)
        self.assertIn('checked', html)

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
        self.assertIn('县 201-486', html)
        self.assertIn('data-place-name="江州縣"', html)
        self.assertIn('checked', html)
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
        self.assertNotIn('CHGIS records match this date filter', html)
        self.assertIn('Polygon Shading', html)
        self.assertIn('polygon-toggle', html)
        self.assertIn('window.CHGISPolygonSelection.apply', html)

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
        self.assertIn('CHGIS records match this date filter', html)
        self.assertIn('are selected', html)
        self.assertIn('mappable records are currently rendered', html)
        self.assertIn('Uncheck records to update the map.', html)
        self.assertIn('name="selected_records"', html)
        self.assertIn('record-toggle', html)
        self.assertIn('window.CHGISMapSelection.apply', html)

    def test_date_only_candidates_can_be_unchecked(self):
        date_rows = filter_data('', (1200, 1200), '', 'counties')
        selected_record_id = str(date_rows.iloc[0]['RECORD_ID'])
        selected_name = str(date_rows.iloc[0]['NAME_FT'])

        with app.test_client() as client:
            response = client.post('/CHGIS_map', data={
                'place_names': '',
                'source_text': '',
                'date': '1200',
                'date_range': '',
                'prefectures': '',
                'counties': 'counties',
                'detected_selection_submitted': '1',
                'selected_records': [selected_record_id],
            })

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(selected_name, html)
        self.assertIn(f'value="{selected_record_id}"', html)
        self.assertIn(f'value="{selected_record_id}" data-place-name="{selected_name}" autocomplete="off" checked', html)
        self.assertNotIn('showing the first 1000', html)

if __name__ == '__main__':
    unittest.main()
