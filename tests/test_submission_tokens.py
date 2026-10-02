"""Characterization tests for the double submit guard.

Every form that creates a record carries a one time token. The first save
claims it; a second request with the same token is recognised and skipped, so
pressing the button twice cannot create the record twice.

The token is written on the caller's cursor, in the same transaction as the
record. A failed save therefore releases the token again and the user can
retry.
"""
import uuid
from decimal import Decimal

from app import claim_submission_token

D = Decimal


def petty_cash_count(factory, project_id):
    factory.cur.execute(
        "SELECT COUNT(*) FROM petty_cash_expenses WHERE project_id = %s",
        (project_id,))
    return factory.cur.fetchone()[0]


# --- the helper itself -----------------------------------------------

def test_a_fresh_token_is_accepted_once(factory, cur):
    token = factory.new_token()

    assert claim_submission_token(cur, token, 'pytest') is True


def test_the_same_token_is_refused_the_second_time(factory, cur):
    token = factory.new_token()

    assert claim_submission_token(cur, token, 'pytest') is True
    assert claim_submission_token(cur, token, 'pytest') is False


def test_two_different_tokens_are_both_accepted(factory, cur):
    first = factory.new_token()
    second = factory.new_token()

    assert claim_submission_token(cur, first, 'pytest') is True
    assert claim_submission_token(cur, second, 'pytest') is True


def test_a_missing_token_never_blocks_a_save(factory, cur):
    """An old cached page has no token. That must not stop a real save."""
    assert claim_submission_token(cur, None, 'pytest') is True
    assert claim_submission_token(cur, '', 'pytest') is True


def test_a_malformed_token_never_blocks_a_save(factory, cur):
    assert claim_submission_token(cur, 'not-a-uuid', 'pytest') is True


def test_the_transaction_stays_usable_after_a_refusal(factory, cur):
    """A conflict must not poison the transaction the record is saved in."""
    token = factory.new_token()
    claim_submission_token(cur, token, 'pytest')
    claim_submission_token(cur, token, 'pytest')

    cur.execute("SELECT 1")
    assert cur.fetchone()[0] == 1


def test_a_rolled_back_save_releases_the_token(factory):
    """The token lives in the same transaction as the record."""
    from app import get_connection
    conn = get_connection()
    own = conn.cursor()
    token = factory.new_token()

    assert claim_submission_token(own, token, 'pytest') is True
    conn.rollback()

    # The first attempt never reached the database, so the retry works.
    assert claim_submission_token(own, token, 'pytest') is True
    conn.rollback()
    own.close()
    conn.close()


# --- through a real route --------------------------------------------

def test_posting_the_same_token_twice_creates_one_record(client, factory):
    project_id = factory.project('cift kayit')
    factory.commit()
    token = factory.new_token()
    form = {'petty_cash_title': 'PYTEST kucuk', 'petty_cash_amount': '250',
            'petty_cash_date': '2026-06-15',
            'petty_cash_description': 'PYTEST', 'submission_token': token}

    first = client.post('/project/%d/petty_cash/add' % project_id, data=form,
                        follow_redirects=True)
    second = client.post('/project/%d/petty_cash/add' % project_id, data=form,
                         follow_redirects=True)

    assert first.status_code == 200 and second.status_code == 200
    assert petty_cash_count(factory, project_id) == 1


def test_two_separate_tokens_create_two_records(client, factory):
    project_id = factory.project('iki kayit')
    factory.commit()
    base = {'petty_cash_title': 'PYTEST kucuk', 'petty_cash_amount': '250',
            'petty_cash_date': '2026-06-15', 'petty_cash_description': 'PYTEST'}

    for _ in range(2):
        form = dict(base, submission_token=factory.new_token())
        client.post('/project/%d/petty_cash/add' % project_id, data=form,
                    follow_redirects=True)

    assert petty_cash_count(factory, project_id) == 2


def test_a_refused_save_leaves_the_token_free(client, factory):
    """An invalid date fails validation, so the token must still work."""
    project_id = factory.project('token serbest')
    factory.commit()
    token = factory.new_token()
    bad = {'petty_cash_title': 'PYTEST kucuk', 'petty_cash_amount': '250',
           'petty_cash_date': '22026-06-15',
           'petty_cash_description': 'PYTEST', 'submission_token': token}

    client.post('/project/%d/petty_cash/add' % project_id, data=bad,
                follow_redirects=True)
    assert petty_cash_count(factory, project_id) == 0

    good = dict(bad, petty_cash_date='2026-06-15')
    client.post('/project/%d/petty_cash/add' % project_id, data=good,
                follow_redirects=True)

    assert petty_cash_count(factory, project_id) == 1
