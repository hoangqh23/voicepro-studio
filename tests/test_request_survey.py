from tools.request_survey import pairs, safe_headers


def test_survey_does_not_record_auth_headers():
    assert safe_headers({'Cookie': 'private', 'Authorization': 'private',
                         'X-CSRFToken': 'private', 'Proxy-Authorization': 'private',
                         'User-Agent': 'browser'}) == {'user-agent': 'browser'}


def test_survey_compares_every_pair_and_does_not_count_unknown_ip_as_duplicate():
    rows = [dict(run=i, create=dict(probe_ip=None, fingerprint_sha256='same'))
            for i in range(1, 51)]
    result = pairs(rows)
    assert len(result) == 1225
    assert all(p['same_fingerprint'] for p in result)
    assert not any(p['same_probe_ip'] or p['same_both'] for p in result)
    rows[0]['create']['probe_ip'] = rows[1]['create']['probe_ip'] = '192.0.2.1'
    assert sum(p['same_both'] for p in pairs(rows)) == 1
    assert not pairs([dict(run=1), rows[0]])[0]['comparable']
