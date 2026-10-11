"""The release notes banner lives on the dashboard and nowhere else.

Closing it is done in the browser (localStorage), so these tests only check
where the banner is rendered and that it carries its storage key.
"""
import pytest

BANNER_ID = 'id="release-notes-banner"'
STORAGE_KEY = 'release_notes_2026_10'


def test_banner_is_on_the_dashboard(client):
    html = client.get('/dashboard').get_data(as_text=True)

    assert BANNER_ID in html
    assert 'data-storage-key="%s"' % STORAGE_KEY in html
    assert 'Uygulamaya yeni özellikler eklendi' in html
    assert 'id="release-notes-close"' in html
    # The notice about the corrected debt figure is set apart from the list.
    assert 'release-notes-info' in html and 'Düzeltme:' in html


@pytest.mark.parametrize('path', [
    '/cashflow', '/check-calendar', '/overdue', '/debts', '/checks',
])
def test_banner_is_not_on_other_pages(client, path):
    response = client.get(path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert BANNER_ID not in html
    assert STORAGE_KEY not in html
