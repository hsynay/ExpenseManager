"""/debts is built on base.html, so the shared page scripts reach it.

The page used to be a standalone HTML file. It then missed the double
submit guard, the jump to the row after an edit and the thousands format,
all of which live in base.html.
"""


def get_debts(client, query=''):
    response = client.get('/debts' + query)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_debts_page_gets_the_shared_scripts_once(client):
    html = get_debts(client)

    assert 'name="csrf-token"' in html
    # Each shared script is there exactly once. A second copy of the scroll
    # script would declare the same 'let' twice and stop the whole script.
    assert html.count('Double submit guard') == 1
    assert html.count('Land on the row the user came from') == 1
    assert html.count('window.initThousandsInput') == 1
    assert html.count('let calcScrollValue') == 1
    assert html.count('id="progress"') == 1
    assert html.count('<nav class="navbar') == 1


def test_debts_page_keeps_its_own_look_and_search_box(client):
    html = get_debts(client, '?search=PYTEST%20yok')

    # The page was designed without static/style.css.
    assert 'static/style.css' not in html
    assert 'padding-top: 76px' in html
    assert '<div class="container my-4">' in html
    assert 'id="debtSearch"' in html
    assert 'value="PYTEST yok"' in html
